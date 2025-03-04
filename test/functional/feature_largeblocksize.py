'''
Test large block sizes in xfield max block size

This test creates large transactions and sends it to node [0].
Blocks are created using generate RPC in multiples of 1MB upto 20 MB
The block is then synched to other nodes [1] and [2]

Nodes 1 and are stopped to avoid mempool synching. When mempool is synched blocks are reconstructed by compact block reconstruction. Here we want to test the node capability to send large messages

'''

import time
from timeit import default_timer as timer
from test_framework.blocktools import create_block, create_coinbase, create_tx_with_large_script
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal, connect_nodes, hex_str_to_bytes
from test_framework.mininode import P2PDataStore
from test_framework.script import MAX_SCRIPT_SIZE
from test_framework.messages import ser_compact_size

SCR_SIZE = len(ser_compact_size(MAX_SCRIPT_SIZE))

reverse_bytes = (lambda txid  : txid[-1: -len(txid)-1: -1])


# TestP2PConn: A peer we use to send messages to bitcoind, and store responses.
class TestP2PConn(P2PDataStore):
    def __init__(self, time_to_connect):
        super().__init__(time_to_connect)
        self.last_sendcmpct = []
        self.block_announced = False
        # Store the hashes of blocks we've seen announced.
        # This is for synchronizing the p2p message traffic,
        # so we can eg wait until a particular block is announced.
        self.announced_blockhashes = set()

class MaxBlockSizeInXFieldTest(BitcoinTestFramework):
    def set_test_params(self):

        self.num_nodes = 3
        self.setup_clean_chain = True
        self.mocktime = int(time.time() - 50)

    def run_test(self):

        self.log.info("Test starting...")
        self.stop_node(1)
        self.stop_node(2)

        #genesis block (B0)
        self.block_time = int(time.time())
        self.nodes[0].generate(10, self.signblockprivkey_wif)

        self.nodes[0].add_p2p_connection(TestP2PConn(self.nodes[0].time_to_connect))
        self.nodes[0].p2p.wait_for_getheaders(timeout=5)

        for (i,size) in enumerate(range(1,7)):
            self.block_time += 1
            block_size = size * 1000000
            self.log.info("Checking Block size %d"%block_size)

            tip  = int(self.nodes[0].getbestblockhash(), 16)
            blocknew = create_block(tip, create_coinbase((i*3)+11), self.block_time )
            blocknew.xfieldType = 2
            blocknew.xfield = block_size + 1000
            blocknew.solve(self.signblockprivkey)
            self.nodes[0].p2p.send_blocks_and_test([blocknew], self.nodes[0], success=True)
            assert_equal(blocknew.hash, self.nodes[0].getbestblockhash())

            self.send_txs_for_large_block(self.nodes[0], blocknew.vtx[0].malfixsha256, block_size)
            blockhex = self.nodes[0].generate(1, self.signblockprivkey_wif)
            self.start_node(1)
            connect_nodes(self.nodes[0], 1)
            self.sync_all([self.nodes[0:2]])
            blocknew = self.nodes[0].getblock(blockhex[0])
            self.nodes[1].generate(1, self.signblockprivkey_wif)
            self.sync_all([self.nodes[0:2]])
            self.stop_node(1)

        self.start_node(1)
        connect_nodes(self.nodes[0], 1)

        self.log.info("Waiting for All nodes to synch...")
        start_time = timer()
        self.start_node(2)
        connect_nodes(self.nodes[0], 2)
        self.sync_all([self.nodes[0:3]])
        stop_time = timer()
        self.log.info("All Node sync took %d seconds"% (stop_time - start_time))



    def send_txs_for_large_block(self, node, spend, size=1000000):
        current_size = 0
        tx_count = 0
        spend_addr = node.getnewaddress()
        scr = hex_str_to_bytes(node.getaddressinfo(spend_addr)['scriptPubKey'])
        while current_size < size:
            tx = create_tx_with_large_script(spend, 0, scr, 10, 25)
            current_size = current_size + len(tx.serialize())
            node.p2p.send_txs_and_test([tx], node, success=True)
            tx_count = tx_count + 1
        self.log.info("    Total tx count %d"%tx_count)


if __name__ == '__main__':
    MaxBlockSizeInXFieldTest().main()
