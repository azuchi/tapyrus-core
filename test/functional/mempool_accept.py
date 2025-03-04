#!/usr/bin/env python3
# Copyright (c) 2017 The Bitcoin Core developers
# Copyright (c) 2019 Chaintope Inc.
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test mempool acceptance of raw transactions."""

from io import BytesIO
import copy
import time
from test_framework.test_framework import BitcoinTestFramework
from test_framework.messages import (
    BIP125_SEQUENCE_NUMBER,
    COIN,
    COutPoint,
    CTransaction,
    CTxOut,
    MAX_BLOCK_BASE_SIZE,
)
from test_framework.script import (
    hash160,
    CScript,
    OP_0,
    OP_RESERVED,
    OP_EQUAL,
    OP_HASH160,
    OP_RETURN,
)
from test_framework.util import (
    assert_equal,
    assert_raises_rpc_error,
    bytes_to_hex_str,
    hex_str_to_bytes,
    wait_until,
)
from test_framework.address import key_to_p2pkh
from test_framework.key import CECKey
from test_framework.blocktools import createTestGenesisBlock

class MempoolAcceptanceTest(BitcoinTestFramework):
    def __init(self):
        self.signblockprivkey = CECKey()
        self.coinbase_key.set_secretbytes(bytes.fromhex("8d5366123cb560bb606379f90a0bfd4769eecc0557f1b362dcae9012b548b1e5"))
        self.signblockpubkey = self.coinbase_key.get_pubkey()
        self.setup_clean_chain = True
        self.genesisBlock = createTestGenesisBlock(self.coinbase_pubkey, self.coinbase_key, int(time.time() - 100))

    def set_test_params(self):
        self.num_nodes = 1
        self.extra_args = [[
            '-txindex',
            '-reindex',  # Need reindex for txindex
        ]] * self.num_nodes

    def check_mempool_result(self, result_expected, *args, **kwargs):
        """Wrapper to check result of testmempoolaccept rpc on node_0's mempool"""
        result_test = self.nodes[0].testmempoolaccept(*args, **kwargs)
        assert_equal(result_expected, result_test)
        assert_equal(self.nodes[0].getmempoolinfo()['size'], self.mempool_size)

    def run_test(self):
        node = self.nodes[0]

        self.log.info('Start with empty mempool, and 100 blocks')
        self.mempool_size = 0
        node.generate(100, self.signblockprivkey_wif)
        assert_equal(node.getmempoolinfo()['size'], self.mempool_size)

        self.log.info('Should not accept garbage to testmempoolaccept')
        assert_raises_rpc_error(-3, 'Expected type array, got string', lambda: node.testmempoolaccept(rawtxs='ff00baar'))
        assert_raises_rpc_error(-22, 'TX decode failed', lambda: node.testmempoolaccept(rawtxs=['ff00baar']))

        self.log.info('A transaction already in the blockchain')
        coin = node.listunspent()[0]  # Pick a random coin(base) to spend
        raw_tx_in_block = node.signrawtransactionwithwallet(node.createrawtransaction(
            inputs=[{'txid': coin['txid'], 'vout': coin['vout']}],
            outputs=[{node.getnewaddress(): 0.3}, {node.getnewaddress(): coin['amount'] - 1}],
        ), [], "ALL", self.options.scheme)['hex']
        txid_in_block = node.sendrawtransaction(hexstring=raw_tx_in_block, allowhighfees=True)
        node.generate(1, self.signblockprivkey_wif)
        self.check_mempool_result(
            result_expected= {txid_in_block: { 'allowed': False, 'reject-reason': '18: txn-already-known'}},
            rawtxs=[raw_tx_in_block],
        )

        self.log.info('A transaction not in the mempool')
        fee = 0.00000700
        raw_tx_0 = node.signrawtransactionwithwallet(node.createrawtransaction(
            inputs=[{"txid": txid_in_block, "vout": 0, "sequence": BIP125_SEQUENCE_NUMBER}],  # RBF is used later
            outputs=[{node.getnewaddress(): 0.3 - fee}],
        ), [], "ALL", self.options.scheme)['hex']
        tx = CTransaction()
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_0)))
        txid_0 = tx.rehash()
        self.mempool_size = 1
        self.check_mempool_result(
            result_expected={ txid_0: { 'allowed': True}},
            rawtxs=[raw_tx_0],
        )

        self.log.info('A transaction in the mempool')
        node.sendrawtransaction(hexstring=raw_tx_0)
        self.mempool_size = 1
        self.check_mempool_result(
            result_expected={txid_0: {'allowed': False, 'reject-reason': '18: txn-already-in-mempool'}},
            rawtxs=[raw_tx_0],
        )

        self.log.info('A transaction that replaces a mempool transaction')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_0)))
        tx.vout[0].nValue -= int(fee * COIN)  # Double the fee
        tx.vin[0].nSequence = BIP125_SEQUENCE_NUMBER + 1  # Now, opt out of RBF
        raw_tx_0 = node.signrawtransactionwithwallet(bytes_to_hex_str(tx.serialize()), [], "ALL", self.options.scheme)['hex']
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_0)))
        txid_0 = tx.rehash()
        self.mempool_size = 1
        self.check_mempool_result(
            result_expected={ txid_0 :{ 'allowed': True}},
            rawtxs=[raw_tx_0],
        )

        self.log.info('A transaction that conflicts with an unconfirmed tx')
        # Send the transaction that replaces the mempool transaction and opts out of replaceability
        node.sendrawtransaction(hexstring=bytes_to_hex_str(tx.serialize()), allowhighfees=True)
        # take original raw_tx_0
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_0)))
        tx.vout[0].nValue -= int(4 * fee * COIN)  # Set more fee
        # skip re-signing the tx
        self.check_mempool_result(
            result_expected={ tx.rehash(): { 'allowed': False, 'reject-reason': '18: txn-mempool-conflict'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
            allowhighfees=True,
        )

        self.log.info('A transaction with missing inputs, that never existed')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_0)))
        tx.vin[0].prevout = COutPoint(hash=int('ff' * 32, 16), n=14)
        # skip re-signing the tx
        self.check_mempool_result(
            result_expected={ tx.rehash():{ 'allowed': False, 'reject-reason': 'missing-inputs'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction with missing inputs, that existed once in the past')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_0)))
        tx.vin[0].prevout.n = 1  # Set vout to 1, to spend the other outpoint (49 coins) of the in-chain-tx we want to double spend
        raw_tx_1 = node.signrawtransactionwithwallet(bytes_to_hex_str(tx.serialize()), [], "ALL", self.options.scheme)['hex']
        txid_1 = node.sendrawtransaction(hexstring=raw_tx_1, allowhighfees=True)
        # Now spend both to "clearly hide" the outputs, ie. remove the coins from the utxo set by spending them
        raw_tx_spend_both = node.signrawtransactionwithwallet(node.createrawtransaction(
            inputs=[
                {'txid': txid_0, 'vout': 0},
                {'txid': txid_1, 'vout': 0},
            ],
            outputs=[{node.getnewaddress(): 0.1}]
        ), [], "ALL", self.options.scheme)['hex']
        txid_spend_both = node.sendrawtransaction(hexstring=raw_tx_spend_both, allowhighfees=True)
        node.generate(1, self.signblockprivkey_wif)
        self.mempool_size = 0
        # Now see if we can add the coins back to the utxo set by sending the exact txs again
        self.check_mempool_result(
            result_expected={txid_0:{ 'allowed': False, 'reject-reason': 'missing-inputs'}},
            rawtxs=[raw_tx_0],
        )
        self.check_mempool_result(
            result_expected={txid_1:{ 'allowed': False, 'reject-reason': 'missing-inputs'}},
            rawtxs=[raw_tx_1],
        )

        self.log.info('Create a signed "reference" tx for later use')
        raw_tx_reference = node.signrawtransactionwithwallet(node.createrawtransaction(
            inputs=[{'txid': txid_spend_both, 'vout': 0}],
            outputs=[{node.getnewaddress(): 0.005}],
        ), [], "ALL", self.options.scheme)['hex']
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_reference)))
        txid = tx.rehash()
        self.mempool_size = 1
        # Reference tx should be valid on itself
        self.check_mempool_result(
            result_expected={txid:{ 'allowed': True}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction with no outputs')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_reference)))
        tx.vout = []
        # Skip re-signing the transaction for context independent checks from now on
        # tx.deserialize(BytesIO(hex_str_to_bytes(node.signrawtransactionwithwallet(bytes_to_hex_str(tx.serialize()))['hex'])))
        self.check_mempool_result(
            result_expected={ tx.rehash():{ 'allowed': False, 'reject-reason': '16: bad-txns-vout-empty'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A really large transaction')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_reference)))
        tx.vin = [tx.vin[0]] * 4 * (MAX_BLOCK_BASE_SIZE // len(tx.vin[0].serialize()))
        self.check_mempool_result(
            result_expected={ tx.rehash():{ 'allowed': False, 'reject-reason': '16: bad-txns-oversize'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction with negative output value')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_reference)))
        tx.vout[0].nValue *= -1
        self.check_mempool_result(
            result_expected={ tx.rehash():{ 'allowed': False, 'reject-reason': '16: bad-txns-vout-negative'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction with too large output value')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_reference)))
        tx.vout[0].nValue = 21000000 * COIN + 1
        self.check_mempool_result(
            result_expected={ tx.rehash():{ 'allowed': False, 'reject-reason': '16: bad-txns-vout-toolarge'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction with too large sum of output values')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_reference)))
        tx.vout = [tx.vout[0]] * 2
        tx.vout[0].nValue = 21000000 * COIN
        self.check_mempool_result(
            result_expected={ tx.rehash():{ 'allowed': False, 'reject-reason': '16: bad-txns-txouttotal-toolarge'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction with duplicate inputs')
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_reference)))
        tx.vin = [tx.vin[0]] * 2
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '16: bad-txns-inputs-duplicate'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A coinbase transaction')
        # Pick the input of the first tx we signed, so it has to be a coinbase tx
        raw_tx_coinbase_spent = node.getrawtransaction(txid=node.decoderawtransaction(hexstring=raw_tx_in_block)['vin'][0]['txid'])
        tx.deserialize(BytesIO(hex_str_to_bytes(raw_tx_coinbase_spent)))
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '16: coinbase'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        tx_new = node.signrawtransactionwithwallet(node.createrawtransaction(
            inputs=[{'txid': txid, 'vout': 0}],
            outputs=[{node.getnewaddress(): 0.001}],
        ), [], "ALL", self.options.scheme)['hex']
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))

        self.log.info('Some nonstandard transactions')
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.nFeatures = 3  # A features currently non-standard
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: features'}},
            rawtxs=[node.signrawtransactionwithwallet(bytes_to_hex_str(tx.serialize()), [], "ALL", self.options.scheme)['hex']],
        )
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vout[0].scriptPubKey = CScript([OP_RESERVED])  # Some non-standard script
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: scriptpubkey'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vout[0].scriptPubKey = CScript([OP_0])  # Some custom script - scriptpubkey passes isStandard check but scriptsig+scriptpubkey fails.
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '16: mandatory-script-verify-flag-failed (Signature must be zero for failed CHECK(MULTI)SIG operation)'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vin[0].scriptSig = CScript([OP_HASH160])  # Some not-pushonly scriptSig
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: scriptsig-not-pushonly'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        output_p2sh_burn = CTxOut(nValue=540, scriptPubKey=CScript([OP_HASH160, hash160(b'burn'), OP_EQUAL]))
        num_scripts = 100000 // len(output_p2sh_burn.serialize())  # Use enough outputs to make the tx too large for our policy
        tx.vout = [output_p2sh_burn] * num_scripts
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: tx-size'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vout[0] = output_p2sh_burn
        tx.vout[0].nValue -= 1  # Make output smaller, such that it is dust for our policy
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: dust'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vout[0].scriptPubKey = CScript([OP_RETURN, b'\xff'])
        tx.vout = [tx.vout[0]] * 2
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: multi-op-return'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A timelocked transaction')
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vin[0].nSequence -= 1  # Should be non-max, so locktime is not ignored
        tx.nLockTime = node.getblockcount() + 1
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: non-final'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction that is locked by BIP68 sequence logic')
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vin[0].nSequence = 2  # We could include it in the second block mined from now, but not the very next one
        # Can skip re-signing the tx because of early rejection
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: non-BIP68-final'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
            allowhighfees=True,
        )

        self.restart_node(0, ["-datacarriermultiple"])
        self.log.info('A transaction with multiple OP_RETURNs in multiple outputs')
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vout[0].scriptPubKey = CScript([OP_RETURN, b'\xff'])
        tx.vout = [tx.vout[0]] * 2
        signedtx = node.signrawtransactionwithwallet(bytes_to_hex_str(tx.serialize()))
        tx.deserialize(BytesIO(hex_str_to_bytes(signedtx['hex'])))
        self.mempool_size = 2
        self.check_mempool_result(
            result_expected={tx.rehash():{'allowed': True}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

        self.log.info('A transaction with multiple OP_RETURNs in one outputs')
        tx.deserialize(BytesIO(hex_str_to_bytes(tx_new)))
        tx.vout[0].scriptPubKey = CScript([OP_RETURN, b'\xff', OP_RETURN, b'\xff'])
        self.check_mempool_result(
            result_expected={tx.rehash():{ 'allowed': False, 'reject-reason': '64: scriptpubkey'}},
            rawtxs=[bytes_to_hex_str(tx.serialize())],
        )

if __name__ == '__main__':
    MempoolAcceptanceTest().main()
