#!/usr/bin/env python3
# Copyright (c) 2014-2016 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or https://www.opensource.org/licenses/mit-license.php .

from test_framework.test_framework import BitcoinTestFramework
from test_framework.authproxy import JSONRPCException
from test_framework.util import assert_equal, assert_greater_than, \
    initialize_chain_clean, start_nodes, connect_nodes_bi, stop_nodes, \
    wait_pastelds

from decimal import Decimal, getcontext
getcontext().prec = 16

# Create one-input, one-output, no-fee transaction:
class RawTransactionsTest(BitcoinTestFramework):

    def __init__(self):
        super().__init__()
        self.setup_clean_chain = True
        self.num_nodes = 4

    def setup_chain(self):
        print("Initializing test directory "+self.options.tmpdir)
        initialize_chain_clean(self.options.tmpdir, self.num_nodes)

    def setup_network(self, split=False):
        self.nodes = start_nodes(self.num_nodes, self.options.tmpdir,
                           extra_args=[['-experimentalfeatures', '-developerencryptwallet']] * 4)

        connect_nodes_bi(self.nodes,0,1)
        connect_nodes_bi(self.nodes,1,2)
        connect_nodes_bi(self.nodes,0,2)
        connect_nodes_bi(self.nodes,0,3)

        self.is_network_split=False
        self.sync_all()

    def run_test(self):
        print("Mining blocks...")
        feeTolerance = self._2patoshi #if the fee's positive delta is higher than this value tests will fail, neg. delta always fail the tests

        self.nodes[2].generate(1)
        self.sync_all()
        self.nodes[0].generate(101)
        self.sync_all()
        self.nodes[0].sendtoaddress(self.nodes[2].getnewaddress(),1.5);
        self.nodes[0].sendtoaddress(self.nodes[2].getnewaddress(),1.0);
        self.nodes[0].sendtoaddress(self.nodes[2].getnewaddress(),5.0);
        self.sync_all()
        self.nodes[0].generate(1)
        self.sync_all()

        ###############
        # simple test #
        ###############
        inputs  = [ ]
        outputs = { self.nodes[0].getnewaddress() : 1.0 }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)
        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        assert_equal(len(dec_tx['vin']) > 0, True) #test if we have enought inputs

        ##############################
        # simple test with two coins #
        ##############################
        inputs  = [ ]
        outputs = { self.nodes[0].getnewaddress() : 2.2 }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        assert_equal(len(dec_tx['vin']) > 0, True) #test if we have enough inputs

        ##############################
        # simple test with two coins #
        ##############################
        inputs  = [ ]
        outputs = { self.nodes[0].getnewaddress() : 2.6 }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        assert_equal(len(dec_tx['vin']) > 0, True)
        assert_equal(dec_tx['vin'][0]['scriptSig']['hex'], '')


        ################################
        # simple test with two outputs #
        ################################
        inputs  = [ ]
        outputs = { self.nodes[0].getnewaddress() : 2.6, self.nodes[1].getnewaddress() : 2.5 }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        totalOut = 0
        for out in dec_tx['vout']:
            totalOut += out['value']

        assert_equal(len(dec_tx['vin']) > 0, True)
        assert_equal(dec_tx['vin'][0]['scriptSig']['hex'], '')


        #########################################################################
        # test a fundrawtransaction with a VIN greater than the required amount #
        #########################################################################
        utx = False
        listunspent = self.nodes[2].listunspent()
        for aUtx in listunspent:
            if aUtx['amount'] == 5.0:
                utx = aUtx
                break

        assert_equal(utx!=False, True)

        inputs  = [ {'txid' : utx['txid'], 'vout' : utx['vout']}]
        outputs = { self.nodes[0].getnewaddress() : Decimal('1.0') }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)
        assert_equal(utx['txid'], dec_tx['vin'][0]['txid'])

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        totalOut = 0
        for out in dec_tx['vout']:
            totalOut += out['value']

        assert_equal(fee + totalOut, utx['amount']) #compare vin total and totalout+fee



        #####################################################################
        # test a fundrawtransaction with which will not get a change output #
        #####################################################################
        utx = False
        listunspent = self.nodes[2].listunspent()
        for aUtx in listunspent:
            if aUtx['amount'] == 5.0:
                utx = aUtx
                break

        assert_equal(utx!=False, True)

        inputs  = [ {'txid' : utx['txid'], 'vout' : utx['vout']}]
        outputs = { self.nodes[0].getnewaddress() : Decimal('5.0') - fee - feeTolerance }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)
        assert_equal(utx['txid'], dec_tx['vin'][0]['txid'])

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        totalOut = 0
        for out in dec_tx['vout']:
            totalOut += out['value']

        assert_equal(rawtxfund['changepos'], -1)
        assert_equal(fee + totalOut, utx['amount']) #compare vin total and totalout+fee



        #########################################################################
        # test a fundrawtransaction with a VIN smaller than the required amount #
        #########################################################################
        utx = False
        listunspent = self.nodes[2].listunspent()
        for aUtx in listunspent:
            if aUtx['amount'] == 1.0:
                utx = aUtx
                break

        assert_equal(utx!=False, True)

        inputs  = [ {'txid' : utx['txid'], 'vout' : utx['vout']}]
        outputs = { self.nodes[0].getnewaddress() : Decimal('1.0') }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)

        # 4-byte version + 1-byte vin count + 36-byte prevout then script_len
        rawtx = rawtx[:82] + "0100" + rawtx[84:]

        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)
        assert_equal(utx['txid'], dec_tx['vin'][0]['txid'])
        assert_equal("00", dec_tx['vin'][0]['scriptSig']['hex'])

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        totalOut = 0
        matchingOuts = 0
        for i, out in enumerate(dec_tx['vout']):
            totalOut += out['value']
            if out['scriptPubKey']['addresses'][0] in outputs:
                matchingOuts+=1
            else:
                assert_equal(i, rawtxfund['changepos'])

        assert_equal(utx['txid'], dec_tx['vin'][0]['txid'])
        assert_equal("00", dec_tx['vin'][0]['scriptSig']['hex'])

        assert_equal(matchingOuts, 1)
        assert_equal(len(dec_tx['vout']), 2)


        ###########################################
        # test a fundrawtransaction with two VINs #
        ###########################################
        utx  = False
        utx2 = False
        listunspent = self.nodes[2].listunspent()
        for aUtx in listunspent:
            if aUtx['amount'] == 1.0:
                utx = aUtx
            if aUtx['amount'] == 5.0:
                utx2 = aUtx


        assert_equal(utx!=False, True)

        inputs  = [ {'txid' : utx['txid'], 'vout' : utx['vout']},{'txid' : utx2['txid'], 'vout' : utx2['vout']} ]
        outputs = { self.nodes[0].getnewaddress() : 6.0 }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)
        assert_equal(utx['txid'], dec_tx['vin'][0]['txid'])

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        totalOut = 0
        matchingOuts = 0
        for out in dec_tx['vout']:
            totalOut += out['value']
            if out['scriptPubKey']['addresses'][0] in outputs:
                matchingOuts+=1

        assert_equal(matchingOuts, 1)
        assert_equal(len(dec_tx['vout']), 2)

        matchingIns = 0
        for vinOut in dec_tx['vin']:
            for vinIn in inputs:
                if vinIn['txid'] == vinOut['txid']:
                    matchingIns+=1

        assert_equal(matchingIns, 2) #we now must see two vins identical to vins given as params

        #########################################################
        # test a fundrawtransaction with two VINs and two vOUTs #
        #########################################################
        utx  = False
        utx2 = False
        listunspent = self.nodes[2].listunspent()
        for aUtx in listunspent:
            if aUtx['amount'] == 1.0:
                utx = aUtx
            if aUtx['amount'] == 5.0:
                utx2 = aUtx


        assert_equal(utx!=False, True)

        inputs  = [ {'txid' : utx['txid'], 'vout' : utx['vout']},{'txid' : utx2['txid'], 'vout' : utx2['vout']} ]
        outputs = { self.nodes[0].getnewaddress() : 6.0, self.nodes[0].getnewaddress() : 1.0 }
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)
        assert_equal(utx['txid'], dec_tx['vin'][0]['txid'])

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        fee = rawtxfund['fee']
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])
        totalOut = 0
        matchingOuts = 0
        for out in dec_tx['vout']:
            totalOut += out['value']
            if out['scriptPubKey']['addresses'][0] in outputs:
                matchingOuts+=1

        assert_equal(matchingOuts, 2)
        assert_equal(len(dec_tx['vout']), 3)

        ##############################################
        # test a fundrawtransaction with invalid vin #
        ##############################################
        listunspent = self.nodes[2].listunspent()
        inputs  = [ {'txid' : "1c7f966dab21119bac53213a2bc7532bff1fa844c124fd750a7d0b1332440bd1", 'vout' : 0} ] #invalid vin!
        outputs = { self.nodes[0].getnewaddress() : 1.0}
        rawtx   = self.nodes[2].createrawtransaction(inputs, outputs)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)

        errorString = ""
        try:
            rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        except JSONRPCException as e:
            errorString = e.error['message']

        assert_equal("Insufficient" in errorString, True)



        ############################################################
        #compare fee of a standard pubkeyhash transaction
        inputs = []
        outputs = {self.nodes[1].getnewaddress():1.1}
        rawTx = self.nodes[0].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[0].fundrawtransaction(rawTx)

        #create same transaction over sendtoaddress
        txId = self.nodes[0].sendtoaddress(self.nodes[1].getnewaddress(), 1.1)
        signedFee = self.nodes[0].getrawmempool(True)[txId]['fee']

        #compare fee
        feeDelta = Decimal(fundedTx['fee']) - Decimal(signedFee)
        assert(feeDelta >= 0 and feeDelta <= feeTolerance)
        ############################################################

        ############################################################
        #compare fee of a standard pubkeyhash transaction with multiple outputs
        inputs = []
        outputs = {self.nodes[1].getnewaddress():1.1,self.nodes[1].getnewaddress():1.2,self.nodes[1].getnewaddress():0.1,self.nodes[1].getnewaddress():1.3,self.nodes[1].getnewaddress():0.2,self.nodes[1].getnewaddress():0.3}
        rawTx = self.nodes[0].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[0].fundrawtransaction(rawTx)
        #create same transaction over sendtoaddress
        txId = self.nodes[0].sendmany("", outputs)
        signedFee = self.nodes[0].getrawmempool(True)[txId]['fee']

        #compare fee
        feeDelta = Decimal(fundedTx['fee']) - Decimal(signedFee)
        assert(feeDelta >= 0 and feeDelta <= feeTolerance)
        ############################################################


        ############################################################
        #compare fee of a 2of2 multisig p2sh transaction

        # create 2of2 addr
        addr1 = self.nodes[1].getnewaddress()
        addr2 = self.nodes[1].getnewaddress()

        addr1Obj = self.nodes[1].validateaddress(addr1)
        addr2Obj = self.nodes[1].validateaddress(addr2)

        mSigObj = self.nodes[1].addmultisigaddress(2, [addr1Obj['pubkey'], addr2Obj['pubkey']])

        inputs = []
        outputs = {mSigObj:1.1}
        rawTx = self.nodes[0].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[0].fundrawtransaction(rawTx)

        #create same transaction over sendtoaddress
        txId = self.nodes[0].sendtoaddress(mSigObj, 1.1)
        signedFee = self.nodes[0].getrawmempool(True)[txId]['fee']

        #compare fee
        feeDelta = Decimal(fundedTx['fee']) - Decimal(signedFee)
        assert(feeDelta >= 0 and feeDelta <= feeTolerance)
        ############################################################


        ############################################################
        #compare fee of a standard pubkeyhash transaction

        # create 4of5 addr
        addr1 = self.nodes[1].getnewaddress()
        addr2 = self.nodes[1].getnewaddress()
        addr3 = self.nodes[1].getnewaddress()
        addr4 = self.nodes[1].getnewaddress()
        addr5 = self.nodes[1].getnewaddress()

        addr1Obj = self.nodes[1].validateaddress(addr1)
        addr2Obj = self.nodes[1].validateaddress(addr2)
        addr3Obj = self.nodes[1].validateaddress(addr3)
        addr4Obj = self.nodes[1].validateaddress(addr4)
        addr5Obj = self.nodes[1].validateaddress(addr5)

        mSigObj = self.nodes[1].addmultisigaddress(4, [addr1Obj['pubkey'], addr2Obj['pubkey'], addr3Obj['pubkey'], addr4Obj['pubkey'], addr5Obj['pubkey']])

        inputs = []
        outputs = {mSigObj:1.1}
        rawTx = self.nodes[0].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[0].fundrawtransaction(rawTx)

        #create same transaction over sendtoaddress
        txId = self.nodes[0].sendtoaddress(mSigObj, 1.1)
        signedFee = self.nodes[0].getrawmempool(True)[txId]['fee']

        #compare fee
        feeDelta = Decimal(fundedTx['fee']) - Decimal(signedFee)
        assert(feeDelta >= 0 and feeDelta <= feeTolerance)
        ############################################################


        ############################################################
        # spend a 2of2 multisig transaction over fundraw

        # create 2of2 addr
        addr1 = self.nodes[2].getnewaddress()
        addr2 = self.nodes[2].getnewaddress()

        addr1Obj = self.nodes[2].validateaddress(addr1)
        addr2Obj = self.nodes[2].validateaddress(addr2)

        mSigObj = self.nodes[2].addmultisigaddress(2, [addr1Obj['pubkey'], addr2Obj['pubkey']])


        # send 1.2 BTC to msig addr
        txId = self.nodes[0].sendtoaddress(mSigObj, 1.2)
        self.sync_all()
        self.nodes[1].generate(1)
        self.sync_all()

        oldBalance = self.nodes[1].getbalance()
        inputs = []
        outputs = {self.nodes[1].getnewaddress():1.1}
        rawTx = self.nodes[2].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[2].fundrawtransaction(rawTx)

        signedTx = self.nodes[2].signrawtransaction(fundedTx['hex'])
        txId = self.nodes[2].sendrawtransaction(signedTx['hex'])
        self.sync_all()
        self.nodes[1].generate(1)
        self.sync_all()

        # make sure funds are received at node1
        assert_equal(oldBalance+Decimal('1.10000'), self.nodes[1].getbalance())

        ############################################################
        # locked wallet test
        self.nodes[1].encryptwallet("test")
        self.nodes.pop(1)
        stop_nodes(self.nodes)
        wait_pastelds()

        self.nodes = start_nodes(self.num_nodes, self.options.tmpdir)

        connect_nodes_bi(self.nodes,0,1)
        connect_nodes_bi(self.nodes,1,2)
        connect_nodes_bi(self.nodes,0,2)
        connect_nodes_bi(self.nodes,0,3)
        self.is_network_split=False
        self.sync_all()

        error = False
        try:
            self.nodes[1].sendtoaddress(self.nodes[0].getnewaddress(), 1.2)
        except:
            error = True
        assert(error)

        oldBalance = self.nodes[0].getbalance()

        inputs = []
        outputs = {self.nodes[0].getnewaddress():1.1}
        rawTx = self.nodes[1].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[1].fundrawtransaction(rawTx)

        #now we need to unlock
        self.nodes[1].walletpassphrase("test", 100)
        signedTx = self.nodes[1].signrawtransaction(fundedTx['hex'])
        txId = self.nodes[1].sendrawtransaction(signedTx['hex'])
        self.sync_all()
        self.nodes[1].generate(1)
        self.sync_all()

        # make sure funds are received at node1
        assert_equal(oldBalance + self._reward + Decimal('1.10000'), self.nodes[0].getbalance())



        ###############################################
        # multiple (~19) inputs tx test | Compare fee #
        ###############################################

        #empty node1, send some small coins from node0 to node1
        self.nodes[1].sendtoaddress(self.nodes[0].getnewaddress(), self.nodes[1].getbalance(), "", "", True);
        self.sync_all()
        self.nodes[0].generate(1)
        self.sync_all()

        for i in range(0,20):
            self.nodes[0].sendtoaddress(self.nodes[1].getnewaddress(), 10)
        self.sync_all()
        self.nodes[0].generate(1)
        self.sync_all()

        #fund a tx with ~20 small inputs
        inputs = []
        outputs = {self.nodes[0].getnewaddress():150,self.nodes[0].getnewaddress():40}
        rawTx = self.nodes[1].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[1].fundrawtransaction(rawTx)

        #create same transaction over sendtoaddress
        txId = self.nodes[1].sendmany("", outputs)
        signedFee = self.nodes[1].getrawmempool(True)[txId]['fee']

        #compare fee
        feeDelta = Decimal(fundedTx['fee']) - Decimal(signedFee)
        assert(feeDelta >= 0 and feeDelta <= feeTolerance*19) #~19 inputs


        #############################################
        # multiple (~19) inputs tx test | sign/send #
        #############################################

        #again, empty node1, send some small coins from node0 to node1
        self.nodes[1].sendtoaddress(self.nodes[0].getnewaddress(), self.nodes[1].getbalance(), "", "", True)
        self.sync_all()
        self.nodes[0].generate(1)
        self.sync_all()

        for i in range(0,20):
            self.nodes[0].sendtoaddress(self.nodes[1].getnewaddress(), 10)
        self.sync_all()
        self.nodes[0].generate(1)
        self.sync_all()

        #fund a tx with ~20 small inputs
        oldBalance = self.nodes[0].getbalance()

        inputs = []
        outputs = {self.nodes[0].getnewaddress():150,self.nodes[0].getnewaddress():40}
        rawTx = self.nodes[1].createrawtransaction(inputs, outputs)
        fundedTx = self.nodes[1].fundrawtransaction(rawTx)
        fundedAndSignedTx = self.nodes[1].signrawtransaction(fundedTx['hex'])
        txId = self.nodes[1].sendrawtransaction(fundedAndSignedTx['hex'])
        self.sync_all()
        self.nodes[0].generate(1)
        self.sync_all()
        assert_equal(oldBalance+self._reward+Decimal('190'), self.nodes[0].getbalance()) #190+block reward

        #####################################################
        # test fundrawtransaction with OP_RETURN and no vin #
        #####################################################

        rawtx   = "0100000000010000000000000000066a047465737400000000"
        dec_tx  = self.nodes[2].decoderawtransaction(rawtx)

        assert_equal(len(dec_tx['vin']), 0)
        assert_equal(len(dec_tx['vout']), 1)

        rawtxfund = self.nodes[2].fundrawtransaction(rawtx)
        dec_tx  = self.nodes[2].decoderawtransaction(rawtxfund['hex'])

        assert_greater_than(len(dec_tx['vin']), 0) # at least one vin
        assert_equal(len(dec_tx['vout']), 2) # one change output added


if __name__ == '__main__':
    RawTransactionsTest().main()
