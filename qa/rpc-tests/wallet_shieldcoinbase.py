#!/usr/bin/env python3
# Copyright (c) 2017 The Zcash developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or https://www.opensource.org/licenses/mit-license.php .

from test_framework.test_framework import BitcoinTestFramework
from test_framework.authproxy import JSONRPCException
from test_framework.util import assert_equal, initialize_chain_clean, \
    start_node, connect_nodes_bi, sync_blocks, sync_mempools, \
    wait_and_assert_operationid_status

from decimal import Decimal, getcontext
getcontext().prec = 16

class WalletShieldCoinbaseTest (BitcoinTestFramework):
    def __init__(self, addr_type):
        super(WalletShieldCoinbaseTest, self).__init__()
        self.addr_type = addr_type

    def setup_chain(self):
        print("Initializing test directory "+self.options.tmpdir)
        initialize_chain_clean(self.options.tmpdir, 4)

    def setup_network(self, split=False):
        args = ['-debug=zrpcunsafe']
        args2 = ['-debug=zrpcunsafe']
        if self.addr_type != 'sprout':
            nu = [
                '-nuparams=5ba81b19:0', # Overwinter
                '-nuparams=76b809bb:1', # Sapling
            ]
            args.extend(nu)
            args2 = args
        self.nodes = []
        self.nodes.append(start_node(0, self.options.tmpdir, args))
        self.nodes.append(start_node(1, self.options.tmpdir, args))
        self.nodes.append(start_node(2, self.options.tmpdir, args2))
        connect_nodes_bi(self.nodes,0,1)
        connect_nodes_bi(self.nodes,1,2)
        connect_nodes_bi(self.nodes,0,2)
        self.is_network_split=False
        self.sync_all()

    def run_test (self):
        print("Mining blocks...")

        self.nodes[0].generate(1)
        do_not_shield_taddr = self.nodes[0].getnewaddress()

        self.nodes[0].generate(4)
        self.sync_all()
        walletinfo = self.nodes[0].getwalletinfo()
        assert_equal(walletinfo['immature_balance'], self._reward*5)
        assert_equal(walletinfo['balance'], 0)
        self.sync_all()
        self.nodes[2].generate(1)
        self.nodes[2].getnewaddress()
        self.nodes[2].generate(1)
        self.nodes[2].getnewaddress()
        self.nodes[2].generate(1)
        self.sync_all()
        self.nodes[1].generate(101)
        self.sync_all()
        assert_equal(self.nodes[0].getbalance(), self._reward*5)
        assert_equal(self.nodes[1].getbalance(), self._reward*1)
        assert_equal(self.nodes[2].getbalance(), self._reward*3)

        # Prepare to send taddr->zaddr
        mytaddr = self.nodes[0].getnewaddress()
        myzaddr = self.nodes[0].z_getnewaddress(self.addr_type)

        # Shielding will fail when trying to spend from watch-only address
        self.nodes[2].importaddress(mytaddr)
        try:
            self.nodes[2].z_shieldcoinbase(mytaddr, myzaddr)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("Could not find any coinbase funds to shield" in errorString, True)

        # Shielding will fail because fee is negative
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, -1)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("Amount out of range" in errorString, True)

        # Shielding will fail because fee is larger than MAX_MONEY
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, self._maxmoney + self._patoshi)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("Amount out of range" in errorString, True)

        # Shielding will fail because fee is larger than sum of utxos
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, 99999)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("Insufficient coinbase funds" in errorString, True)

        # Shielding will fail because limit parameter must be at least 0
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, self._fee, -1)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("Limit on maximum number of utxos cannot be negative" in errorString, True)

        # Shielding will fail because limit parameter is absurdly large
        try:
            self.nodes[0].z_shieldcoinbase("*", myzaddr, self._fee, 99999999999999)
        except JSONRPCException as e:
            errorString = e.error['message']
        assert_equal("JSON integer out of range" in errorString, True)

        # Shield coinbase utxos from node 0 of value 40, standard fee of 0.10000
        result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr)
        wait_and_assert_operationid_status(self.nodes[0], result['opid'])
        self.sync_all()
        self.nodes[1].generate(1)
        self.sync_all()

        # Confirm balances and that do_not_shield_taddr containing funds of 10 was left alone
        assert_equal(self.nodes[0].getbalance(), self._reward)
        assert_equal(self.nodes[0].z_getbalance(do_not_shield_taddr), self._reward)
        assert_equal(self.nodes[0].z_getbalance(myzaddr), self._reward*4 - self._fee)
        assert_equal(self.nodes[1].getbalance(), self._reward*2)
        assert_equal(self.nodes[2].getbalance(), self._reward*3)

        # Shield coinbase utxos from any node 2 taddr, and set fee to 0
        result = self.nodes[2].z_shieldcoinbase("*", myzaddr, 0)
        wait_and_assert_operationid_status(self.nodes[2], result['opid'])
        self.sync_all()
        self.nodes[1].generate(1)
        self.sync_all()

        assert_equal(self.nodes[0].getbalance(), self._reward)
        assert_equal(self.nodes[0].z_getbalance(myzaddr), self._reward*7 - self._fee)
        assert_equal(self.nodes[1].getbalance(), self._reward*3)
        assert_equal(self.nodes[2].getbalance(), 0)

        # Generate 800 coinbase utxos on node 0, and 20 coinbase utxos on node 2
        self.nodes[0].generate(800)
        self.sync_all()
        self.nodes[2].generate(20)
        self.sync_all()
        self.nodes[1].generate(100)
        self.sync_all()
        mytaddr = self.nodes[0].getnewaddress()

        def verify_locking(first, second, limit):
            result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, 0, limit)
            assert_equal(result["shieldingUTXOs"], Decimal(first))
            assert_equal(result["remainingUTXOs"], Decimal(second))
            remainingValue = result["remainingValue"]
            opid1 = result['opid']

            # Verify that utxos are locked (not available for selection) by queuing up another shielding operation
            result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, 0, 0)
            assert_equal(result["shieldingValue"], Decimal(remainingValue))
            assert_equal(result["shieldingUTXOs"], Decimal(second))
            assert_equal(result["remainingValue"], Decimal('0'))
            assert_equal(result["remainingUTXOs"], Decimal('0'))
            opid2 = result['opid']

            # wait for both aysnc operations to complete
            wait_and_assert_operationid_status(self.nodes[0], opid1)
            wait_and_assert_operationid_status(self.nodes[0], opid2)

        if self.addr_type == 'sprout':
            # Shielding the 800 utxos will occur over two transactions, since max tx size is 100,000 bytes.
            # We don't verify shieldingValue as utxos are not selected in any specific order, so value can change on each test run.
            # We set an unrealistically high limit parameter of 99999, to verify that max tx size will constrain the number of utxos.
            verify_locking('662', '138', 99999)
        else:
            # Shield the 800 utxos over two transactions
            verify_locking('500', '300', 500)

        # sync_all() invokes sync_mempool() but node 2's mempool limit will cause tx1 and tx2 to be rejected.
        # So instead, we sync on blocks and mempool for node 0 and node 1, and after a new block is generated
        # which mines tx1 and tx2, all nodes will have an empty mempool which can then be synced.
        sync_blocks(self.nodes[:2])
        sync_mempools(self.nodes[:2])
        self.nodes[1].generate(1)
        self.sync_all()

        # Verify maximum number of utxos which node 0 can shield is set by default limit parameter of 50
        self.nodes[0].generate(200)
        self.sync_all()
        mytaddr = self.nodes[0].getnewaddress()
        result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, self._fee)
        assert_equal(result["shieldingUTXOs"], Decimal('50'))
        assert_equal(result["remainingUTXOs"], Decimal('50'))
        wait_and_assert_operationid_status(self.nodes[0], result['opid'])

        # Verify maximum number of utxos which node 0 can shield can be set by the limit parameter
        result = self.nodes[0].z_shieldcoinbase(mytaddr, myzaddr, self._fee, 33)
        assert_equal(result["shieldingUTXOs"], Decimal('33'))
        assert_equal(result["remainingUTXOs"], Decimal('17'))
        wait_and_assert_operationid_status(self.nodes[0], result['opid'])
        sync_blocks(self.nodes)
        sync_mempools(self.nodes)
        self.nodes[1].generate(1)
        self.sync_all()
