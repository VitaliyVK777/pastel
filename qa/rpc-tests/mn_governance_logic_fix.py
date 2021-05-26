#!/usr/bin/env python3
# Copyright (c) 2018-2021 The Pastel Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal, assert_greater_than, initialize_chain_clean, \
    initialize_datadir, start_nodes, start_node, connect_nodes_bi, \
    pasteld_processes, wait_and_assert_operationid_status, p2p_port, \
    stop_node
from mn_common import MasterNodeCommon

import os
import sys
import time

from decimal import Decimal, getcontext
getcontext().prec = 16

# 12 Master Nodes
private_keys_list = ["91sY9h4AQ62bAhNk1aJ7uJeSnQzSFtz7QmW5imrKmiACm7QJLXe", #0 
                     "923JtwGJqK6mwmzVkLiG6mbLkhk1ofKE1addiM8CYpCHFdHDNGo", #1
                     "91wLgtFJxdSRLJGTtbzns5YQYFtyYLwHhqgj19qnrLCa1j5Hp5Z", #2
                     "92XctTrjQbRwEAAMNEwKqbiSAJsBNuiR2B8vhkzDX4ZWQXrckZv", #3
                     "923JCnYet1pNehN6Dy4Ddta1cXnmpSiZSLbtB9sMRM1r85TWym6", #4
                     "93BdbmxmGp6EtxFEX17FNqs2rQfLD5FMPWoN1W58KEQR24p8A6j", #5
                     "92av9uhRBgwv5ugeNDrioyDJ6TADrM6SP7xoEqGMnRPn25nzviq", #6
                     "91oHXFR2NVpUtBiJk37i8oBMChaQRbGjhnzWjN9KQ8LeAW7JBdN", #7
                     "92MwGh67mKTcPPTCMpBA6tPkEE5AK3ydd87VPn8rNxtzCmYf9Yb", #8
                     "92VSXXnFgArfsiQwuzxSAjSRuDkuirE1Vf7KvSX7JE51dExXtrc", #9
                     "91hruvJfyRFjo7JMKnAPqCXAMiJqecSfzn9vKWBck2bKJ9CCRuo", #10
                     "92sYv5JQHzn3UDU6sYe5kWdoSWEc6B98nyY5JN7FnTTreP8UNrq"  #11
                    ]

class MasterNodeGovernanceTest (MasterNodeCommon):
    number_of_master_nodes = len(private_keys_list)
    number_of_simple_nodes = 2
    total_number_of_nodes = number_of_master_nodes+number_of_simple_nodes
    mining_node_num = number_of_master_nodes
    hot_node_num = number_of_master_nodes+1

    def setup_chain(self):
        print("Initializing test directory "+self.options.tmpdir)
        initialize_chain_clean(self.options.tmpdir, self.total_number_of_nodes)

    def setup_network(self, split=False):
        self.nodes = []
        self.is_network_split = False
        self.setup_masternodes_network(private_keys_list, self.number_of_simple_nodes)

    def run_test (self):
        self.mining_enough(self.mining_node_num, self.number_of_master_nodes)
        cold_nodes = {k: v for k, v in enumerate(private_keys_list)}
        _, _, _ = self.start_mn(self.mining_node_num, self.hot_node_num, cold_nodes, self.total_number_of_nodes)

        self.reconnect_nodes(0, self.number_of_master_nodes)
        self.sync_all()

        print("Register first ticket")
        #####################################
        #              NODE 0               #
        ######################################

        # NODE #0: 1. First ticket registration
        address1 = self.nodes[0].getnewaddress()
        res1 = self.nodes[0].governance("ticket", "add", address1, "1000", "test", "yes")
        assert_equal(res1['result'], 'successful')
        ticket1_id = res1['ticketId']
        print(ticket1_id)
        # ticket1_id now: 1 yes

        #This is a failed TICKET re-REGISTRATION with YES 
        print("NODE #0: This is a failed TICKET re-REGISTRATION with YES")
        res1 = self.nodes[0].governance("ticket", "add", address1, "1000", "test", "yes")
        assert_equal(res1['result'], 'failed')

        #This is a failed TICKET re-REGISTRATION with NO 
        print("NODE #0: This is a failed TICKET re-REGISTRATION with NO")
        res1 = self.nodes[0].governance("ticket", "add", address1, "1000", "test", "no")
        assert_equal(res1['result'], 'failed')

        #This is a failed TICKET VOTE with YES - already voted yes
        print("NODE #0: This is a failed TICKET VOTE with YES - already voted yes")
        res1 = self.nodes[0].governance("ticket", "vote", ticket1_id, "yes")
        assert_equal(res1['result'], 'failed')
        
        #This is a failed TICKET VOTE with NO - one-time change shall be possible only
        print("NODE #0: This is a passed TICKET VOTE with NO - first change to: no")
        res1 = self.nodes[0].governance("ticket", "vote", ticket1_id, "no")
        assert_equal(res1['result'], 'successful')
        # ticket1_id now: 0 yes

        #MINIG
        print ("Minig...")
        self.slow_mine(2, 10, 2, 0.5)
        #This is a failed TICKET VOTE with NO - one-time change shall be possible
        print("NODE #0: This is a failed TICKET VOTE with NO - already voted no in another block")
        res1 = self.nodes[0].governance("ticket", "vote", ticket1_id, "no")
        assert_equal(res1['result'], 'failed')

        #This is a failed TICKET VOTE with YES - already voted yes in another block
        print("NODE #0: This is a failed TICKET VOTE with YES - already voted with yes in another block")
        res1 = self.nodes[0].governance("ticket", "vote", ticket1_id, "yes")
        assert_equal(res1['result'], 'failed')

        #If we reach this point it means we did not allow it to vote
        print("Yes! It was not able to vote !")

        time.sleep(3)
        print ("Let's have some votes and ticket registration from another nodes")
        #####################################
        #              NODE 1               #
        #####################################    
        # First vote 'NO' with NODE 1    
        print("NODE #1: This is a passed TICKET VOTE with NO - from Node #1")
        res1 = self.nodes[1].governance("ticket", "vote", ticket1_id, "no")
        assert_equal(res1['result'], 'successful')

        # Vote again in the same block but this time it is a first change, so acceppted
        print("NODE #1: This is a passed TICKET VOTE with YES - first change(n->y), from Node #1")
        res1 = self.nodes[1].governance("ticket", "vote", ticket1_id, "yes")
        assert_equal(res1['result'], 'successful')
        # ticket1_id now: 1 yes

        print ("Minig...")
        self.slow_mine(2, 10, 2, 0.5)

        #Now new blocks are mined and it should not accept the voting again.
        print("NODE #1: This is a failed TICKET re-VOTE with YES - already there, from Node #1")
        res1 = self.nodes[1].governance("ticket", "vote", ticket1_id, "yes")
        assert_equal(res1['result'], 'failed')

        #Now new blocks are mined and it should not accept the voting again.
        print("NODE #1: This is a failed TICKET re-VOTE with No - already voted, from Node #1")
        res1 = self.nodes[1].governance("ticket", "vote", ticket1_id, "no")
        assert_equal(res1['result'], 'failed')

        
        #Node #1 shall not re-register already existing ticket
        print("NODE #1: This is a failed TICKET re-Registration from Node #1")
        res1 = self.nodes[1].governance("ticket", "add", address1, "1000", "test", "no")
        assert_equal(res1['result'], 'failed')

        time.sleep(3)
        #####################################
        #              NODE 2               #
        #####################################  
        #Node #2 shall not re-register already existing ticket
        print("NODE #2: This is a failed TICKET re-Registration from Node #2")
        res1 = self.nodes[2].governance("ticket", "add", address1, "1000", "test", "no")
        assert_equal(res1['result'], 'failed')

         # First vote 'NO' with NODE 2 - passed
        print("NODE #2: This is a passed TICKET VOTE with NO - from Node #2")
        res1 = self.nodes[2].governance("ticket", "vote", ticket1_id, "no")
        assert_equal(res1['result'], 'successful')

        # First vote 'YES' with NODE 2 - passed 
        print("NODE #2: This is a passed TICKET VOTE with YES - from Node #2")
        res1 = self.nodes[2].governance("ticket", "vote", ticket1_id, "yes")
        assert_equal(res1['result'], 'successful')
        # ticket1_id now: 2 yes

        # Second change it is not allowed
        print("NODE #2: This is a failed TICKET VOTE with NO - from Node #2")
        res1 = self.nodes[2].governance("ticket", "vote", ticket1_id, "no")
        assert_equal(res1['result'], 'failed')

        print ("Minig...")
        self.slow_mine(2, 10, 2, 0.5)

        # Second change it is not allowed
        print("NODE #2: This is a failed TICKET VOTE with NO - from Node #2 - already voted in another block")
        res1 = self.nodes[2].governance("ticket", "vote", ticket1_id, "no")
        assert_equal(res1['result'], 'failed')
        time.sleep(3)
        
        #Only active masternode can vote or add ticket!
        res1 = self.nodes[self.mining_node_num].governance("ticket", "add", address1, str(self.collateral), "test", "no")
        assert_equal(res1['result'], 'failed')

        res1 = self.nodes[self.mining_node_num].governance("ticket", "vote", ticket1_id, "yes")
        assert_equal(res1['result'], 'failed')

        address2 = self.nodes[self.mining_node_num].getnewaddress()
        res1 = self.nodes[self.mining_node_num].governance("ticket", "add", address2, str(self.collateral), "test", "yes")
        assert_equal(res1['errorMessage'], "Only Active Master Node can vote")

        time.sleep(3)

        print("Register second ticket")
        #2. Second ticket
        res1 = self.nodes[2].governance("ticket", "add", address2, "2000", "test", "yes")
        assert_equal(res1['result'], 'successful')
        ticket2_id = res1['ticketId']

        self.nodes[self.mining_node_num].generate(5)

        print("Waiting 60 seconds")
        time.sleep(60)

        print("Test tickets votes")
        #3. Preliminary test, should be 2 tickets: 1st ticket - 3 votes, 2 yes; 2nd ticket - 1 vote, 1 yes
        for i in range(0, self.total_number_of_nodes):
            res1 = self.nodes[i].governance("list", "tickets")
            print(res1)
            for j in range(0, 2):
                if res1[j]['id'] == ticket1_id:
                    print(res1[j]['ticket'])
                    assert_equal("Total votes: 3, Yes votes: 2" in res1[j]['ticket'], True)
                elif res1[j]['id'] == ticket2_id:
                    print(res1[j]['ticket'])
                    assert_equal("Total votes: 1, Yes votes: 1" in res1[j]['ticket'], True)
                else:
                    assert_equal(res1[0]['id'], res1[1]['id'])

    #Implement "mining" actually
    def slow_mine(self, number_of_bursts, num_in_each_burst, wait_between_bursts, wait_inside_burst):
        for x in range(number_of_bursts):
            for y in range(num_in_each_burst):
                self.nodes[self.mining_node_num].generate(1)
                time.sleep(wait_inside_burst)
            time.sleep(wait_between_bursts)

if __name__ == '__main__':
    MasterNodeGovernanceTest ().main ()
