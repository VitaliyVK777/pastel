#!/usr/bin/env python3
# Copyright (c) 2010 ArtForz -- public domain half-a-node
# Copyright (c) 2012 Jeff Garzik
# Copyright (c) 2010-2016 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or https://www.opensource.org/licenses/mit-license.php .

#
# mininode.py - Pastel P2P network half-a-node
#
# This python code was modified from ArtForz' public domain  half-a-node, as
# found in the mini-node branch of https://github.com/jgarzik/pynode.
#
# NodeConn: an object which manages p2p connectivity to a bitcoin node
# NodeConnCB: a base class that describes the interface for receiving
#             callbacks with network messages from a NodeConn
# CBlock, CTransaction, CBlockHeader, CTxIn, CTxOut, etc....:
#     data structures that should map to corresponding structures in
#     bitcoin/primitives
# msg_block, msg_tx, msg_headers, etc.:
#     data structures that represent network messages
# ser_*, deser_*: functions that handle serialization/deserialization


import struct
import socket
import asyncore
import time
import sys
import random
from binascii import hexlify
from io import BytesIO
import hashlib
from threading import RLock
from threading import Thread
import logging
import copy
from pyblake2 import blake2b

from .equihash import (
    gbp_basic,
    gbp_validate,
    hash_nonce,
    zcash_person,
)

BIP0031_VERSION = 60000
SPROUT_PROTO_VERSION = 170002  # past bip-31 for ping/pong
OVERWINTER_PROTO_VERSION = 170003
SAPLING_PROTO_VERSION = 170006
BLOSSOM_PROTO_VERSION = 170008

MY_SUBVERSION = b"/python-mininode-tester:0.0.2/"

SPROUT_VERSION_GROUP_ID = 0x00000000
OVERWINTER_VERSION_GROUP_ID = 0x03C48270
SAPLING_VERSION_GROUP_ID = 0x892F2085
# No transaction format change in Blossom.

MAX_INV_SZ = 50000


COIN = 100000 # 1 PSL in patoshis

# Keep our own socket map for asyncore, so that we can track disconnects
# ourselves (to workaround an issue with closing an asyncore socket when
# using select)
mininode_socket_map = dict()

# One lock for synchronizing all data access between the networking thread (see
# NetworkThread below) and the thread running the test logic.  For simplicity,
# NodeConn acquires this lock whenever delivering a message to to a NodeConnCB,
# and whenever adding anything to the send buffer (in send_message()).  This
# lock should be acquired in the thread running the test logic to synchronize
# access to any data shared with the NodeConnCB or NodeConn.
mininode_lock = RLock()

# Serialization/deserialization tools
def sha256(s):
    return hashlib.new('sha256', s).digest()

def hash256(s):
    return sha256(sha256(s))

def nuparams(branch_id, height):
    return '-nuparams=%x:%d' % (branch_id, height)


def ser_compactsize(n):
    if n < 253:
        return struct.pack("B", n)
    elif n < 0x10000:
        return struct.pack("<BH", 253, n)
    elif n < 0x100000000:
        return struct.pack("<BI", 254, n)
    return struct.pack("<BQ", 255, n)


def deser_compactsize(f):
    size = struct.unpack("<B", f.read(1))[0]
    if size == 253:
        size = struct.unpack("<H", f.read(2))[0]
    elif size == 254:
        size = struct.unpack("<I", f.read(4))[0]
    elif size == 255:
        size = struct.unpack("<Q", f.read(8))[0]
    return size


def deser_string(f):
    size = deser_compactsize(f)
    return f.read(size)


def ser_string(s):
    if isinstance(s, bytes):
        st = s
    else:
        st = bytearray(s, 'utf-8')
    return ser_compactsize(len(st)) + st


def deser_uint256(f):
    r = 0
    for i in range(8):
        t = struct.unpack("<I", f.read(4))[0]
        r += t << (i * 32)
    return r


def ser_uint256(u):
    rs = b""
    for i in range(8):
        rs += struct.pack("<I", u & 0xFFFFFFFF)
        u >>= 32
    return rs


def uint256_from_str(s):
    r = 0
    t = struct.unpack("<IIIIIIII", s[:32])
    for i in range(8):
        r += t[i] << (i * 32)
    return r


def uint256_from_compact(c):
    nbytes = (c >> 24) & 0xFF
    v = (c & 0xFFFFFF) << (8 * (nbytes - 3))
    return v


def deser_vector(f, c):
    size = deser_compactsize(f)
    r = []
    for i in range(size):
        t = c()
        t.deserialize(f)
        r.append(t)
    return r


def ser_vector(l):
    r = ser_compactsize(len(l))
    for i in l:
        r += i.serialize()
    return r


def deser_uint256_vector(f):
    size = deser_compactsize(f)
    r = []
    for i in range(size):
        t = deser_uint256(f)
        r.append(t)
    return r


def ser_uint256_vector(l):
    r = ser_compactsize(len(l))
    for i in l:
        r += ser_uint256(i)
    return r


def deser_string_vector(f):
    size = deser_compactsize(f)
    r = []
    for i in range(size):
        t = deser_string(f)
        r.append(t)
    return r


def ser_string_vector(l):
    r = ser_compactsize(len(l))
    for sv in l:
        r += ser_string(sv)
    return r


def deser_int_vector(f):
    size = deser_compactsize(f)
    r = []
    for i in range(size):
        t = struct.unpack("<i", f.read(4))[0]
        r.append(t)
    return r


def ser_int_vector(l):
    r = ser_compactsize(len(l))
    for i in l:
        r += struct.pack("<i", i)
    return r

def deser_char_vector(f):
    size = deser_compactsize(f)
    r = []
    for i in range(size):
        t = struct.unpack("<B", f.read(1))[0]
        r.append(t)
    return r


def ser_char_vector(l):
    r = ser_compactsize(len(l))
    for i in l:
        r += struct.pack("B", i)
    return r

# Objects that map to pasteld objects, which can be serialized/deserialized

class CAddress(object):
    def __init__(self):
        self.nServices = 1
        self.pchReserved = b"\x00" * 10 + b"\xff" * 2
        self.ip = "0.0.0.0"
        self.port = 0

    def deserialize(self, f):
        self.nServices = struct.unpack("<Q", f.read(8))[0]
        self.pchReserved = f.read(12)
        self.ip = socket.inet_ntoa(f.read(4))
        self.port = struct.unpack(">H", f.read(2))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.nServices)
        r += self.pchReserved
        r += socket.inet_aton(self.ip)
        r += struct.pack(">H", self.port)
        return r

    def __repr__(self):
        return "CAddress(nServices=%i ip=%s port=%i)" % (self.nServices,
                                                         self.ip, self.port)


class CInv(object):
    typemap = {
        0: b"Error",
        1: b"TX",
        2: b"Block"}

    def __init__(self, t=0, h=0):
        self.type = t
        self.hash = h

    def deserialize(self, f):
        self.type = struct.unpack("<i", f.read(4))[0]
        self.hash = deser_uint256(f)

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.type)
        r += ser_uint256(self.hash)
        return r

    def __repr__(self):
        return "CInv(type=%s hash=%064x)" \
            % (self.typemap[self.type], self.hash)


class CBlockLocator(object):
    def __init__(self):
        self.nVersion = SPROUT_PROTO_VERSION
        self.vHave = []

    def deserialize(self, f):
        self.nVersion = struct.unpack("<i", f.read(4))[0]
        self.vHave = deser_uint256_vector(f)

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nVersion)
        r += ser_uint256_vector(self.vHave)
        return r

    def __repr__(self):
        return "CBlockLocator(nVersion=%i vHave=%r)" \
            % (self.nVersion, self.vHave)


class SpendDescription(object):
    def __init__(self):
        self.cv = None
        self.anchor = None
        self.nullifier = None
        self.rk = None
        self.zkproof = None
        self.spendAuthSig = None

    def deserialize(self, f):
        self.cv = deser_uint256(f)
        self.anchor = deser_uint256(f)
        self.nullifier = deser_uint256(f)
        self.rk = deser_uint256(f)
        self.zkproof = f.read(192)
        self.spendAuthSig = f.read(64)

    def serialize(self):
        r = b""
        r += ser_uint256(self.cv)
        r += ser_uint256(self.anchor)
        r += ser_uint256(self.nullifier)
        r += ser_uint256(self.rk)
        r += self.zkproof
        r += self.spendAuthSig
        return r

    def __repr__(self):
        return "SpendDescription(cv=%064x, anchor=%064x, nullifier=%064x, rk=%064x, zkproof=%064x, spendAuthSig=%064x)" \
            % (self.cv, self.anchor, self.nullifier, self.rk, self.zkproof, self.spendauthsig)


class OutputDescription(object):
    def __init__(self):
        self.cv = None
        self.cmu = None
        self.ephemeralKey = None
        self.encCiphertext = None
        self.outCiphertext = None
        self.zkproof = None

    def deserialize(self, f):
        self.cv = deser_uint256(f)
        self.cmu = deser_uint256(f)
        self.ephemeralKey = deser_uint256(f)
        self.encCiphertext = f.read(580)
        self.outCiphertext = f.read(80)
        self.zkproof = f.read(192)

    def serialize(self):
        r = b""
        r += ser_uint256(self.cv)
        r += ser_uint256(self.cmu)
        r += ser_uint256(self.ephemeralKey)
        r += self.encCiphertext
        r += self.outCiphertext
        r += self.zkproof
        return r

    def __repr__(self):
        return "OutputDescription(cv=%064x, cmu=%064x, ephemeralKey=%064x, encCiphertext=%064x, outCiphertext=%064x, zkproof=%064x)" \
            % (self.cv, self.cmu, self.ephemeralKey, self.encCiphertext, self.outCiphertext, self.zkproof)


G1_PREFIX_MASK = 0x02
G2_PREFIX_MASK = 0x0a

class ZCProof(object):
    def __init__(self):
        self.g_A = None
        self.g_A_prime = None
        self.g_B = None
        self.g_B_prime = None
        self.g_C = None
        self.g_C_prime = None
        self.g_K = None
        self.g_H = None

    def deserialize(self, f):
        def deser_g1(self, f):
            leadingByte = struct.unpack("<B", f.read(1))[0]
            return {
                'y_lsb': leadingByte & 1,
                'x': f.read(32),
            }
        def deser_g2(self, f):
            leadingByte = struct.unpack("<B", f.read(1))[0]
            return {
                'y_gt': leadingByte & 1,
                'x': f.read(64),
            }
        self.g_A = deser_g1(f)
        self.g_A_prime = deser_g1(f)
        self.g_B = deser_g2(f)
        self.g_B_prime = deser_g1(f)
        self.g_C = deser_g1(f)
        self.g_C_prime = deser_g1(f)
        self.g_K = deser_g1(f)
        self.g_H = deser_g1(f)

    def serialize(self):
        def ser_g1(self, p):
            return chr(G1_PREFIX_MASK | p['y_lsb']) + p['x']
        def ser_g2(self, p):
            return chr(G2_PREFIX_MASK | p['y_gt']) + p['x']
        r = b""
        r += ser_g1(self.g_A)
        r += ser_g1(self.g_A_prime)
        r += ser_g2(self.g_B)
        r += ser_g1(self.g_B_prime)
        r += ser_g1(self.g_C)
        r += ser_g1(self.g_C_prime)
        r += ser_g1(self.g_K)
        r += ser_g1(self.g_H)
        return r

    def __repr__(self):
        return "ZCProof(g_A=%r g_A_prime=%r g_B=%r g_B_prime=%r g_C=%r g_C_prime=%r g_K=%r g_H=%r)" \
            % (self.g_A, self.g_A_prime,
               self.g_B, self.g_B_prime,
               self.g_C, self.g_C_prime,
               self.g_K, self.g_H)


ZC_NUM_JS_INPUTS = 2
ZC_NUM_JS_OUTPUTS = 2

ZC_NOTEPLAINTEXT_LEADING = 1
ZC_V_SIZE = 8
ZC_RHO_SIZE = 32
ZC_R_SIZE = 32
ZC_MEMO_SIZE = 512

ZC_NOTEPLAINTEXT_SIZE = (
  ZC_NOTEPLAINTEXT_LEADING +
  ZC_V_SIZE +
  ZC_RHO_SIZE +
  ZC_R_SIZE +
  ZC_MEMO_SIZE
)

NOTEENCRYPTION_AUTH_BYTES = 16

ZC_NOTECIPHERTEXT_SIZE = (
  ZC_NOTEPLAINTEXT_SIZE +
  NOTEENCRYPTION_AUTH_BYTES
)

class JSDescription(object):
    def __init__(self):
        self.vpub_old = 0
        self.vpub_new = 0
        self.anchor = 0
        self.nullifiers = [0] * ZC_NUM_JS_INPUTS
        self.commitments = [0] * ZC_NUM_JS_OUTPUTS
        self.onetimePubKey = 0
        self.randomSeed = 0
        self.macs = [0] * ZC_NUM_JS_INPUTS
        self.proof = None
        self.ciphertexts = [None] * ZC_NUM_JS_OUTPUTS

    def deserialize(self, f):
        self.vpub_old = struct.unpack("<q", f.read(8))[0]
        self.vpub_new = struct.unpack("<q", f.read(8))[0]
        self.anchor = deser_uint256(f)

        self.nullifiers = []
        for i in range(ZC_NUM_JS_INPUTS):
            self.nullifiers.append(deser_uint256(f))

        self.commitments = []
        for i in range(ZC_NUM_JS_OUTPUTS):
            self.commitments.append(deser_uint256(f))

        self.onetimePubKey = deser_uint256(f)
        self.randomSeed = deser_uint256(f)

        self.macs = []
        for i in range(ZC_NUM_JS_INPUTS):
            self.macs.append(deser_uint256(f))

        self.proof = ZCProof()
        self.proof.deserialize(f)

        self.ciphertexts = []
        for i in range(ZC_NUM_JS_OUTPUTS):
            self.ciphertexts.append(f.read(ZC_NOTECIPHERTEXT_SIZE))

    def serialize(self):
        r = b""
        r += struct.pack("<q", self.vpub_old)
        r += struct.pack("<q", self.vpub_new)
        r += ser_uint256(self.anchor)
        for i in range(ZC_NUM_JS_INPUTS):
            r += ser_uint256(self.nullifiers[i])
        for i in range(ZC_NUM_JS_OUTPUTS):
            r += ser_uint256(self.commitments[i])
        r += ser_uint256(self.onetimePubKey)
        r += ser_uint256(self.randomSeed)
        for i in range(ZC_NUM_JS_INPUTS):
            r += ser_uint256(self.macs[i])
        r += self.proof.serialize()
        for i in range(ZC_NUM_JS_OUTPUTS):
            r += ser_uint256(self.ciphertexts[i])
        return r

    def __repr__(self):
        return "JSDescription(vpub_old=%i.%08i vpub_new=%i.%08i anchor=%064x onetimePubKey=%064x randomSeed=%064x proof=%r)" \
            % (self.vpub_old, self.vpub_new, self.anchor,
               self.onetimePubKey, self.randomSeed, self.proof)

class COutPoint(object):
    def __init__(self, hash=0, n=0):
        self.hash = hash
        self.n = n

    def deserialize(self, f):
        self.hash = deser_uint256(f)
        self.n = struct.unpack("<I", f.read(4))[0]

    def serialize(self):
        r = b""
        r += ser_uint256(self.hash)
        r += struct.pack("<I", self.n)
        return r

    def __repr__(self):
        return "COutPoint(hash=%064x n=%i)" % (self.hash, self.n)


class CTxIn(object):
    def __init__(self, outpoint=None, scriptSig=b"", nSequence=0):
        if outpoint is None:
            self.prevout = COutPoint()
        else:
            self.prevout = outpoint
        self.scriptSig = scriptSig
        self.nSequence = nSequence

    def deserialize(self, f):
        self.prevout = COutPoint()
        self.prevout.deserialize(f)
        self.scriptSig = deser_string(f)
        self.nSequence = struct.unpack("<I", f.read(4))[0]

    def serialize(self):
        r = b""
        r += self.prevout.serialize()
        r += ser_string(self.scriptSig)
        r += struct.pack("<I", self.nSequence)
        return r

    def __repr__(self):
        return "CTxIn(prevout=%s scriptSig=%s nSequence=%i)" \
            % (self.prevout, hexlify(self.scriptSig),
               self.nSequence)


class CTxOut(object):
    def __init__(self, nValue=0, scriptPubKey=b""):
        self.nValue = nValue
        self.scriptPubKey = scriptPubKey

    def deserialize(self, f):
        self.nValue = struct.unpack("<q", f.read(8))[0]
        self.scriptPubKey = deser_string(f)

    def serialize(self):
        r = b""
        r += struct.pack("<q", self.nValue)
        r += ser_string(self.scriptPubKey)
        return r

    def __repr__(self):
        return "CTxOut(nValue=%i.%08i scriptPubKey=%s)" \
            % (self.nValue // COIN, self.nValue % COIN,
               hexlify(self.scriptPubKey))


class CTransaction(object):
    def __init__(self, tx=None):
        if tx is None:
            self.fOverwintered = True
            self.nVersion = 3
            self.nVersionGroupId = OVERWINTER_VERSION_GROUP_ID
            self.vin = []
            self.vout = []
            self.nLockTime = 0
            self.nExpiryHeight = 0
            self.valueBalance = 0
            self.shieldedSpends = []
            self.shieldedOutputs = []
            self.vJoinSplit = []
            self.joinSplitPubKey = None
            self.joinSplitSig = None
            self.bindingSig = None
            self.sha256 = None
            self.hash = None
        else:
            self.fOverwintered = tx.fOverwintered
            self.nVersion = tx.nVersion
            self.nVersionGroupId = tx.nVersionGroupId
            self.vin = copy.deepcopy(tx.vin)
            self.vout = copy.deepcopy(tx.vout)
            self.nLockTime = tx.nLockTime
            self.nExpiryHeight = tx.nExpiryHeight
            self.valueBalance = tx.valueBalance
            self.shieldedSpends = copy.deepcopy(tx.shieldedSpends)
            self.shieldedOutputs = copy.deepcopy(tx.shieldedOutputs)
            self.vJoinSplit = copy.deepcopy(tx.vJoinSplit)
            self.joinSplitPubKey = tx.joinSplitPubKey
            self.joinSplitSig = tx.joinSplitSig
            self.bindingSig = tx.bindingSig
            self.sha256 = None
            self.hash = None

    def deserialize(self, f):
        header = struct.unpack("<I", f.read(4))[0]
        self.fOverwintered = bool(header >> 31)
        self.nVersion = header & 0x7FFFFFFF
        self.nVersionGroupId = (struct.unpack("<I", f.read(4))[0]
                                if self.fOverwintered else 0)

        isOverwinterV3 = (self.fOverwintered and
                          self.nVersionGroupId == OVERWINTER_VERSION_GROUP_ID and
                          self.nVersion == 3)
        isSaplingV4 = (self.fOverwintered and
                       self.nVersionGroupId == SAPLING_VERSION_GROUP_ID and
                       self.nVersion == 4)

        self.vin = deser_vector(f, CTxIn)
        self.vout = deser_vector(f, CTxOut)
        self.nLockTime = struct.unpack("<I", f.read(4))[0]
        if isOverwinterV3 or isSaplingV4:
            self.nExpiryHeight = struct.unpack("<I", f.read(4))[0]

        if isSaplingV4:
            self.valueBalance = struct.unpack("<q", f.read(8))[0]
            self.shieldedSpends = deser_vector(f, SpendDescription)
            self.shieldedOutputs = deser_vector(f, OutputDescription)

        if self.nVersion >= 2:
            self.vJoinSplit = deser_vector(f, JSDescription)
            if len(self.vJoinSplit) > 0:
                self.joinSplitPubKey = deser_uint256(f)
                self.joinSplitSig = f.read(64)

        if isSaplingV4 and not (len(self.shieldedSpends) == 0 and len(self.shieldedOutputs) == 0):
            self.bindingSig = f.read(64)

        self.sha256 = None
        self.hash = None

    def serialize(self):
        header = (int(self.fOverwintered)<<31) | self.nVersion
        isOverwinterV3 = (self.fOverwintered and
                          self.nVersionGroupId == OVERWINTER_VERSION_GROUP_ID and
                          self.nVersion == 3)
        isSaplingV4 = (self.fOverwintered and
                       self.nVersionGroupId == SAPLING_VERSION_GROUP_ID and
                       self.nVersion == 4)

        r = b""
        r += struct.pack("<I", header)
        if self.fOverwintered:
            r += struct.pack("<I", self.nVersionGroupId)
        r += ser_vector(self.vin)
        r += ser_vector(self.vout)
        r += struct.pack("<I", self.nLockTime)
        if isOverwinterV3 or isSaplingV4:
            r += struct.pack("<I", self.nExpiryHeight)
        if isSaplingV4:
            r += struct.pack("<q", self.valueBalance)
            r += ser_vector(self.shieldedSpends)
            r += ser_vector(self.shieldedOutputs)
        if self.nVersion >= 2:
            r += ser_vector(self.vJoinSplit)
            if len(self.vJoinSplit) > 0:
                r += ser_uint256(self.joinSplitPubKey)
                r += self.joinSplitSig
        if isSaplingV4 and not (len(self.shieldedSpends) == 0 and len(self.shieldedOutputs) == 0):
            r += self.bindingSig
        return r

    def rehash(self):
        self.sha256 = None
        self.calc_sha256()

    def calc_sha256(self):
        if self.sha256 is None:
            self.sha256 = uint256_from_str(hash256(self.serialize()))
        self.hash = hash256(self.serialize())[::-1].hex()

    def is_valid(self):
        self.calc_sha256()
        for tout in self.vout:
            if tout.nValue < 0 or tout.nValue > 21000000 * 100000000:
                return False
        return True

    def __repr__(self):
        r = ("CTransaction(fOverwintered=%r nVersion=%i nVersionGroupId=0x%08x "
             "vin=%r vout=%r nLockTime=%i nExpiryHeight=%i "
             "valueBalance=%i shieldedSpends=%r shieldedOutputs=%r"
             % (self.fOverwintered, self.nVersion, self.nVersionGroupId,
                self.vin, self.vout, self.nLockTime, self.nExpiryHeight,
                self.valueBalance, self.shieldedSpends, self.shieldedOutputs))
        if self.nVersion >= 2:
            r += " vJoinSplit=%r" % (self.vJoinSplit,)
            if len(self.vJoinSplit) > 0:
                r += " joinSplitPubKey=%064x joinSplitSig=%064x" \
                    % (self.joinSplitPubKey, self.joinSplitSig)
        if len(self.shieldedSpends) > 0 or len(self.shieldedOutputs) > 0:
            r += " bindingSig=%064x" % (self.bindingSig,)
        r += ")"
        return r


class CBlockHeader(object):
    def __init__(self, header=None):
        if header is None:
            self.set_null()
        else:
            self.nVersion = header.nVersion
            self.hashPrevBlock = header.hashPrevBlock
            self.hashMerkleRoot = header.hashMerkleRoot
            self.hashFinalSaplingRoot = header.hashFinalSaplingRoot
            self.nTime = header.nTime
            self.nBits = header.nBits
            self.nNonce = header.nNonce
            self.nSolution = header.nSolution
            self.sha256 = header.sha256
            self.hash = header.hash
            self.calc_sha256()

    def set_null(self):
        self.nVersion = 3
        self.hashPrevBlock = 0
        self.hashMerkleRoot = 0
        self.hashFinalSaplingRoot = 0
        self.nTime = 0
        self.nBits = 0
        self.nNonce = 0
        self.nSolution = []
        self.sha256 = None
        self.hash = None

    def deserialize(self, f):
        self.nVersion = struct.unpack("<i", f.read(4))[0]
        self.hashPrevBlock = deser_uint256(f)
        self.hashMerkleRoot = deser_uint256(f)
        self.hashFinalSaplingRoot = deser_uint256(f)
        self.nTime = struct.unpack("<I", f.read(4))[0]
        self.nBits = struct.unpack("<I", f.read(4))[0]
        self.nNonce = deser_uint256(f)
        self.nSolution = deser_char_vector(f)
        self.sha256 = None
        self.hash = None

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nVersion)
        r += ser_uint256(self.hashPrevBlock)
        r += ser_uint256(self.hashMerkleRoot)
        r += ser_uint256(self.hashFinalSaplingRoot)
        r += struct.pack("<I", self.nTime)
        r += struct.pack("<I", self.nBits)
        r += ser_uint256(self.nNonce)
        r += ser_char_vector(self.nSolution)
        return r

    def calc_sha256(self):
        if self.sha256 is None:
            r = b""
            r += struct.pack("<i", self.nVersion)
            r += ser_uint256(self.hashPrevBlock)
            r += ser_uint256(self.hashMerkleRoot)
            r += ser_uint256(self.hashFinalSaplingRoot)
            r += struct.pack("<I", self.nTime)
            r += struct.pack("<I", self.nBits)
            r += ser_uint256(self.nNonce)
            r += ser_char_vector(self.nSolution)
            self.sha256 = uint256_from_str(hash256(r))
            self.hash = hash256(r)[::-1].hex()

    def rehash(self):
        self.sha256 = None
        self.calc_sha256()
        return self.sha256

    def __repr__(self):
        return "CBlockHeader(nVersion=%i hashPrevBlock=%064x hashMerkleRoot=%064x hashFinalSaplingRoot=%064x nTime=%s nBits=%08x nNonce=%064x nSolution=%r)" \
            % (self.nVersion, self.hashPrevBlock, self.hashMerkleRoot, self.hashFinalSaplingRoot,
               time.ctime(self.nTime), self.nBits, self.nNonce, self.nSolution)


class CBlock(CBlockHeader):
    def __init__(self, header=None):
        super(CBlock, self).__init__(header)
        self.vtx = []

    def deserialize(self, f):
        super(CBlock, self).deserialize(f)
        self.vtx = deser_vector(f, CTransaction)

    def serialize(self):
        r = b""
        r += super(CBlock, self).serialize()
        r += ser_vector(self.vtx)
        return r

    def calc_merkle_root(self):
        hashes = []
        for tx in self.vtx:
            tx.calc_sha256()
            hashes.append(ser_uint256(tx.sha256))
        while len(hashes) > 1:
            newhashes = []
            for i in range(0, len(hashes), 2):
                i2 = min(i+1, len(hashes)-1)
                newhashes.append(hash256(hashes[i] + hashes[i2]))
            hashes = newhashes
        return uint256_from_str(hashes[0])

    def is_valid(self, n=48, k=5):
        # H(I||...
        digest = blake2b(digest_size=(512//n)*n//8, person=zcash_person(n, k))
        digest.update(super(CBlock, self).serialize()[:108])
        hash_nonce(digest, self.nNonce)
        if not gbp_validate(self.nSolution, digest, n, k):
            return False
        self.calc_sha256()
        target = uint256_from_compact(self.nBits)
        if self.sha256 > target:
            return False
        for tx in self.vtx:
            if not tx.is_valid():
                return False
        if self.calc_merkle_root() != self.hashMerkleRoot:
            return False
        return True

    def solve(self, n=48, k=5):
        target = uint256_from_compact(self.nBits)
        # H(I||...
        digest = blake2b(digest_size=(512//n)*n//8, person=zcash_person(n, k))
        digest.update(super(CBlock, self).serialize()[:108])
        self.nNonce = 0
        while True:
            # H(I||V||...
            curr_digest = digest.copy()
            hash_nonce(curr_digest, self.nNonce)
            # (x_1, x_2, ...) = A(I, V, n, k)
            solns = gbp_basic(curr_digest, n, k)
            for soln in solns:
                assert(gbp_validate(curr_digest, soln, n, k))
                self.nSolution = soln
                self.rehash()
                if self.sha256 <= target:
                    return
            self.nNonce += 1

    def __repr__(self):
        return "CBlock(nVersion=%i hashPrevBlock=%064x hashMerkleRoot=%064x hashFinalSaplingRoot=%064x nTime=%s nBits=%08x nNonce=%064x nSolution=%r vtx=%r)" \
            % (self.nVersion, self.hashPrevBlock, self.hashMerkleRoot,
               self.hashFinalSaplingRoot, time.ctime(self.nTime), self.nBits,
               self.nNonce, self.nSolution, self.vtx)


class CUnsignedAlert(object):
    def __init__(self):
        self.nVersion = 1
        self.nRelayUntil = 0
        self.nExpiration = 0
        self.nID = 0
        self.nCancel = 0
        self.setCancel = []
        self.nMinVer = 0
        self.nMaxVer = 0
        self.setSubVer = []
        self.nPriority = 0
        self.strComment = b""
        self.strStatusBar = b""
        self.strReserved = b""

    def deserialize(self, f):
        self.nVersion = struct.unpack("<i", f.read(4))[0]
        self.nRelayUntil = struct.unpack("<q", f.read(8))[0]
        self.nExpiration = struct.unpack("<q", f.read(8))[0]
        self.nID = struct.unpack("<i", f.read(4))[0]
        self.nCancel = struct.unpack("<i", f.read(4))[0]
        self.setCancel = deser_int_vector(f)
        self.nMinVer = struct.unpack("<i", f.read(4))[0]
        self.nMaxVer = struct.unpack("<i", f.read(4))[0]
        self.setSubVer = deser_string_vector(f)
        self.nPriority = struct.unpack("<i", f.read(4))[0]
        self.strComment = deser_string(f)
        self.strStatusBar = deser_string(f)
        self.strReserved = deser_string(f)

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nVersion)
        r += struct.pack("<q", self.nRelayUntil)
        r += struct.pack("<q", self.nExpiration)
        r += struct.pack("<i", self.nID)
        r += struct.pack("<i", self.nCancel)
        r += ser_int_vector(self.setCancel)
        r += struct.pack("<i", self.nMinVer)
        r += struct.pack("<i", self.nMaxVer)
        r += ser_string_vector(self.setSubVer)
        r += struct.pack("<i", self.nPriority)
        r += ser_string(self.strComment)
        r += ser_string(self.strStatusBar)
        r += ser_string(self.strReserved)
        return r

    def __repr__(self):
        return "CUnsignedAlert(nVersion %d, nRelayUntil %d, nExpiration %d, nID %d, nCancel %d, nMinVer %d, nMaxVer %d, nPriority %d, strComment %s, strStatusBar %s, strReserved %s)" \
            % (self.nVersion, self.nRelayUntil, self.nExpiration, self.nID,
               self.nCancel, self.nMinVer, self.nMaxVer, self.nPriority,
               self.strComment, self.strStatusBar, self.strReserved)


class CAlert(object):
    def __init__(self):
        self.vchMsg = b""
        self.vchSig = b""

    def deserialize(self, f):
        self.vchMsg = deser_string(f)
        self.vchSig = deser_string(f)

    def serialize(self):
        r = b""
        r += ser_string(self.vchMsg)
        r += ser_string(self.vchSig)
        return r

    def __repr__(self):
        return "CAlert(vchMsg.sz %d, vchSig.sz %d)" \
            % (len(self.vchMsg), len(self.vchSig))


# Objects that correspond to messages on the wire
class msg_version(object):
    command = b"version"

    def __init__(self, protocol_version=BLOSSOM_PROTO_VERSION):
        self.nVersion = protocol_version
        self.nServices = 1
        self.nTime = int(time.time())
        self.addrTo = CAddress()
        self.addrFrom = CAddress()
        self.nNonce = random.getrandbits(64)
        self.strSubVer = MY_SUBVERSION
        self.nStartingHeight = -1

    def deserialize(self, f):
        self.nVersion = struct.unpack("<i", f.read(4))[0]
        if self.nVersion == 10300:
            self.nVersion = 300
        self.nServices = struct.unpack("<Q", f.read(8))[0]
        self.nTime = struct.unpack("<q", f.read(8))[0]
        self.addrTo = CAddress()
        self.addrTo.deserialize(f)
        if self.nVersion >= 106:
            self.addrFrom = CAddress()
            self.addrFrom.deserialize(f)
            self.nNonce = struct.unpack("<Q", f.read(8))[0]
            self.strSubVer = deser_string(f)
            if self.nVersion >= 209:
                self.nStartingHeight = struct.unpack("<i", f.read(4))[0]
            else:
                self.nStartingHeight = None
        else:
            self.addrFrom = None
            self.nNonce = None
            self.strSubVer = None
            self.nStartingHeight = None

    def serialize(self):
        r = b""
        r += struct.pack("<i", self.nVersion)
        r += struct.pack("<Q", self.nServices)
        r += struct.pack("<q", self.nTime)
        r += self.addrTo.serialize()
        r += self.addrFrom.serialize()
        r += struct.pack("<Q", self.nNonce)
        r += ser_string(self.strSubVer)
        r += struct.pack("<i", self.nStartingHeight)
        return r

    def __repr__(self):
        return 'msg_version(nVersion=%i nServices=%i nTime=%s addrTo=%s addrFrom=%s nNonce=0x%016X strSubVer=%s nStartingHeight=%i)' \
            % (self.nVersion, self.nServices, time.ctime(self.nTime),
               self.addrTo, self.addrFrom, self.nNonce,
               self.strSubVer, self.nStartingHeight)


class msg_verack(object):
    command = b"verack"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_verack()"


class msg_addr(object):
    command = b"addr"

    def __init__(self):
        self.addrs = []

    def deserialize(self, f):
        self.addrs = deser_vector(f, CAddress)

    def serialize(self):
        return ser_vector(self.addrs)

    def __repr__(self):
        return "msg_addr(addrs=%r)" % (self.addrs,)


class msg_alert(object):
    command = b"alert"

    def __init__(self):
        self.alert = CAlert()

    def deserialize(self, f):
        self.alert = CAlert()
        self.alert.deserialize(f)

    def serialize(self):
        r = b""
        r += self.alert.serialize()
        return r

    def __repr__(self):
        return "msg_alert(alert=%s)" % (repr(self.alert), )


class msg_inv(object):
    command = b"inv"

    def __init__(self, inv=None):
        if inv is None:
            self.inv = []
        else:
            self.inv = inv

    def deserialize(self, f):
        self.inv = deser_vector(f, CInv)

    def serialize(self):
        return ser_vector(self.inv)

    def __repr__(self):
        return "msg_inv(inv=%s)" % (repr(self.inv))


class msg_getdata(object):
    command = b"getdata"

    def __init__(self, inv=None):
        self.inv = inv if inv != None else []

    def deserialize(self, f):
        self.inv = deser_vector(f, CInv)

    def serialize(self):
        return ser_vector(self.inv)

    def __repr__(self):
        return "msg_getdata(inv=%s)" % (repr(self.inv))


class msg_notfound(object):
    command = b"notfound"

    def __init__(self):
        self.inv = []

    def deserialize(self, f):
        self.inv = deser_vector(f, CInv)

    def serialize(self):
        return ser_vector(self.inv)

    def __repr__(self):
        return "msg_notfound(inv=%r)" % (self.inv,)


class msg_getblocks(object):
    command = b"getblocks"

    def __init__(self):
        self.locator = CBlockLocator()
        self.hashstop = 0

    def deserialize(self, f):
        self.locator = CBlockLocator()
        self.locator.deserialize(f)
        self.hashstop = deser_uint256(f)

    def serialize(self):
        r = b""
        r += self.locator.serialize()
        r += ser_uint256(self.hashstop)
        return r

    def __repr__(self):
        return "msg_getblocks(locator=%s hashstop=%064x)" \
            % (repr(self.locator), self.hashstop)


class msg_tx(object):
    command = b"tx"

    def __init__(self, tx=CTransaction()):
        self.tx = tx

    def deserialize(self, f):
        self.tx.deserialize(f)

    def serialize(self):
        return self.tx.serialize()

    def __repr__(self):
        return "msg_tx(tx=%s)" % (repr(self.tx))


class msg_block(object):
    command = b"block"

    def __init__(self, block=None):
        if block is None:
            self.block = CBlock()
        else:
            self.block = block

    def deserialize(self, f):
        self.block.deserialize(f)

    def serialize(self):
        return self.block.serialize()

    def __repr__(self):
        return "msg_block(block=%s)" % (repr(self.block))


class msg_getaddr(object):
    command = b"getaddr"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_getaddr()"


class msg_ping_prebip31(object):
    command = b"ping"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_ping() (pre-bip31)"


class msg_ping(object):
    command = b"ping"

    def __init__(self, nonce=0):
        self.nonce = nonce

    def deserialize(self, f):
        self.nonce = struct.unpack("<Q", f.read(8))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.nonce)
        return r

    def __repr__(self):
        return "msg_ping(nonce=%08x)" % self.nonce


class msg_pong(object):
    command = b"pong"

    def __init__(self, nonce=0):
        self.nonce = nonce

    def deserialize(self, f):
        self.nonce = struct.unpack("<Q", f.read(8))[0]

    def serialize(self):
        r = b""
        r += struct.pack("<Q", self.nonce)
        return r

    def __repr__(self):
        return "msg_pong(nonce=%08x)" % self.nonce


class msg_mempool(object):
    command = b"mempool"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_mempool()"


# getheaders message has
# number of entries
# vector of hashes
# hash_stop (hash of last desired block header, 0 to get as many as possible)
class msg_getheaders(object):
    command = b"getheaders"

    def __init__(self):
        self.locator = CBlockLocator()
        self.hashstop = 0

    def deserialize(self, f):
        self.locator = CBlockLocator()
        self.locator.deserialize(f)
        self.hashstop = deser_uint256(f)

    def serialize(self):
        r = b""
        r += self.locator.serialize()
        r += ser_uint256(self.hashstop)
        return r

    def __repr__(self):
        return "msg_getheaders(locator=%s, stop=%064x)" \
            % (repr(self.locator), self.hashstop)


# headers message has
# <count> <vector of block headers>
class msg_headers(object):
    command = b"headers"

    def __init__(self):
        self.headers = []

    def deserialize(self, f):
        # comment in pasteld indicates these should be deserialized as blocks
        blocks = deser_vector(f, CBlock)
        for x in blocks:
            self.headers.append(CBlockHeader(x))

    def serialize(self):
        blocks = [CBlock(x) for x in self.headers]
        return ser_vector(blocks)

    def __repr__(self):
        return "msg_headers(headers=%s)" % repr(self.headers)


class msg_reject(object):
    command = b"reject"

    def __init__(self):
        self.message = b""
        self.code = 0
        self.reason = b""
        self.data = 0

    def deserialize(self, f):
        self.message = deser_string(f)
        self.code = struct.unpack("<B", f.read(1))[0]
        self.reason = deser_string(f)
        if (self.message == b"block" or self.message == b"tx"):
            self.data = deser_uint256(f)

    def serialize(self):
        r = ser_string(self.message)
        r += struct.pack("<B", self.code)
        r += ser_string(self.reason)
        if (self.message == b"block" or self.message == b"tx"):
            r += ser_uint256(self.data)
        return r

    def __repr__(self):
        return "msg_reject: %s %d %s [%064x]" \
            % (self.message, self.code, self.reason, self.data)


class msg_filteradd(object):
    command = b"filteradd"

    def __init__(self):
        self.data = b""

    def deserialize(self, f):
        self.data = deser_string(f)

    def serialize(self):
        return ser_string(self.data)

    def __repr__(self):
        return "msg_filteradd(data=%r)" % (self.data,)


class msg_filterclear(object):
    command = b"filterclear"

    def __init__(self):
        pass

    def deserialize(self, f):
        pass

    def serialize(self):
        return b""

    def __repr__(self):
        return "msg_filterclear()"


# This is what a callback should look like for NodeConn
# Reimplement the on_* functions to provide handling for events
class NodeConnCB(object):
    def __init__(self):
        self.verack_received = False

    # Derived classes should call this function once to set the message map
    # which associates the derived classes' functions to incoming messages
    def create_callback_map(self):
        self.cbmap = {
            b"version": self.on_version,
            b"verack": self.on_verack,
            b"addr": self.on_addr,
            b"alert": self.on_alert,
            b"inv": self.on_inv,
            b"getdata": self.on_getdata,
            b"notfound": self.on_notfound,
            b"getblocks": self.on_getblocks,
            b"tx": self.on_tx,
            b"block": self.on_block,
            b"getaddr": self.on_getaddr,
            b"ping": self.on_ping,
            b"pong": self.on_pong,
            b"headers": self.on_headers,
            b"getheaders": self.on_getheaders,
            b"reject": self.on_reject,
            b"mempool": self.on_mempool
        }

    def deliver(self, conn, message):
        with mininode_lock:
            try:
                self.cbmap[message.command](conn, message)
            except Exception as e:
                print("ERROR delivering %r (%r)" % (message, e))

    def on_version(self, conn, message):
        if message.nVersion >= 209:
            conn.send_message(msg_verack())
        conn.ver_send = min(BLOSSOM_PROTO_VERSION, message.nVersion)
        if message.nVersion < 209:
            conn.ver_recv = conn.ver_send

    def on_verack(self, conn, message):
        conn.ver_recv = conn.ver_send
        self.verack_received = True

    def on_inv(self, conn, message):
        want = msg_getdata()
        for i in message.inv:
            if i.type != 0:
                want.inv.append(i)
        if len(want.inv):
            conn.send_message(want)

    def on_addr(self, conn, message): pass
    def on_alert(self, conn, message): pass
    def on_getdata(self, conn, message): pass
    def on_notfound(self, conn, message): pass
    def on_getblocks(self, conn, message): pass
    def on_tx(self, conn, message): pass
    def on_block(self, conn, message): pass
    def on_getaddr(self, conn, message): pass
    def on_headers(self, conn, message): pass
    def on_getheaders(self, conn, message): pass
    def on_ping(self, conn, message):
        if conn.ver_send > BIP0031_VERSION:
            conn.send_message(msg_pong(message.nonce))
    def on_reject(self, conn, message): pass
    def on_close(self, conn): pass
    def on_mempool(self, conn): pass
    def on_pong(self, conn, message): pass


# The actual NodeConn class
# This class provides an interface for a p2p connection to a specified node
class NodeConn(asyncore.dispatcher):
    messagemap = {
        b"version": msg_version,
        b"verack": msg_verack,
        b"addr": msg_addr,
        b"alert": msg_alert,
        b"inv": msg_inv,
        b"getdata": msg_getdata,
        b"notfound": msg_notfound,
        b"getblocks": msg_getblocks,
        b"tx": msg_tx,
        b"block": msg_block,
        b"getaddr": msg_getaddr,
        b"ping": msg_ping,
        b"pong": msg_pong,
        b"headers": msg_headers,
        b"getheaders": msg_getheaders,
        b"reject": msg_reject,
        b"mempool": msg_mempool
    }
    MAGIC_BYTES = {
        "mainnet" : b"\x6b\xda\xb8\xfc",  # mainnet
        "testnet3": b"\x38\xb6\xfe\x64",  # testnet3
        "regtest" : b"\xcd\xd8\xfa\x9e"   # regtest
   
    }

    def __init__(self, dstaddr, dstport, rpc, callback, net="regtest", protocol_version=BLOSSOM_PROTO_VERSION):
        asyncore.dispatcher.__init__(self, map=mininode_socket_map)
        self.log = logging.getLogger("NodeConn(%s:%d)" % (dstaddr, dstport))
        self.dstaddr = dstaddr
        self.dstport = dstport
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sendbuf = b""
        self.recvbuf = b""
        self.ver_send = 209
        self.ver_recv = 209
        self.last_sent = 0
        self.state = "connecting"
        self.network = net
        self.cb = callback
        self.disconnect = False

        # stuff version msg into sendbuf
        vt = msg_version(protocol_version)
        vt.addrTo.ip = self.dstaddr
        vt.addrTo.port = self.dstport
        vt.addrFrom.ip = "0.0.0.0"
        vt.addrFrom.port = 0
        self.send_message(vt, True)
        print('MiniNode: Connecting to Pastel Node IP # ' + dstaddr + ':' \
            + str(dstport) + ' using version ' + str(protocol_version))

        try:
            self.connect((dstaddr, dstport))
        except:
            self.handle_close()
        self.rpc = rpc

    def show_debug_msg(self, msg):
        self.log.debug(msg)

    def handle_connect(self):
        self.show_debug_msg("MiniNode: Connected & Listening: \n")
        self.state = b"connected"

    def handle_close(self):
        self.show_debug_msg("MiniNode: Closing Connection to %s:%d... "
                            % (self.dstaddr, self.dstport))
        self.state = b"closed"
        self.recvbuf = b""
        self.sendbuf = b""
        try:
            self.close()
        except:
            pass
        self.cb.on_close(self)

    def handle_read(self):
        try:
            t = self.recv(8192)
            if len(t) > 0:
                self.recvbuf += t
                self.got_data()
        except:
            pass

    def readable(self):
        return True

    def writable(self):
        with mininode_lock:
            length = len(self.sendbuf)
        return (length > 0)

    def handle_write(self):
        with mininode_lock:
            try:
                sent = self.send(self.sendbuf)
            except:
                self.handle_close()
                return
            self.sendbuf = self.sendbuf[sent:]

    def got_data(self):
        try:
            command = b''
            while True:
                if len(self.recvbuf) < 4:
                    return
                if self.recvbuf[:4] != self.MAGIC_BYTES[self.network]:
                    raise ValueError("got garbage %r" % (self.recvbuf,))
                if self.ver_recv < 209:
                    if len(self.recvbuf) < 4 + 12 + 4:
                        return
                    command = self.recvbuf[4:4+12].split(b"\x00", 1)[0]
                    msglen = struct.unpack("<i", self.recvbuf[4+12:4+12+4])[0]
                    checksum = None
                    if len(self.recvbuf) < 4 + 12 + 4 + msglen:
                        return
                    msg = self.recvbuf[4+12+4:4+12+4+msglen]
                    self.recvbuf = self.recvbuf[4+12+4+msglen:]
                else:
                    if len(self.recvbuf) < 4 + 12 + 4 + 4:
                        return
                    command = self.recvbuf[4:4+12].split(b"\x00", 1)[0]
                    msglen = struct.unpack("<i", self.recvbuf[4+12:4+12+4])[0]
                    checksum = self.recvbuf[4+12+4:4+12+4+4]
                    if len(self.recvbuf) < 4 + 12 + 4 + 4 + msglen:
                        return
                    msg = self.recvbuf[4+12+4+4:4+12+4+4+msglen]
                    th = sha256(msg)
                    h = sha256(th)
                    if checksum != h[:4]:
                        raise ValueError("got bad checksum %r" % (self.recvbuf,))
                    self.recvbuf = self.recvbuf[4+12+4+4+msglen:]
                if command in self.messagemap:
                    f = BytesIO(msg)
                    t = self.messagemap[command]()
                    t.deserialize(f)
                    self.got_message(t)
                else:
                    self.show_debug_msg(f'Unknown command: "{command}" {msg!r}')
        except Exception as e:
            if command:
                print(f'got_data({command}): {e!r}')
            else:
                print(f'got_data: {e!r}')
            # import  traceback
            # traceback.print_tb(sys.exc_info()[2])

    def send_message(self, message, pushbuf=False):
        if self.state != b"connected" and not pushbuf:
            return
        self.show_debug_msg("Send %s" % repr(message))
        command = message.command
        data = message.serialize()
        tmsg = self.MAGIC_BYTES[self.network]
        tmsg += command
        tmsg += b"\x00" * (12 - len(command))
        tmsg += struct.pack("<I", len(data))
        if self.ver_send >= 209:
            th = sha256(data)
            h = sha256(th)
            tmsg += h[:4]
        tmsg += data
        with mininode_lock:
            self.sendbuf += tmsg
            self.last_sent = time.time()

    def got_message(self, message):
        if message.command == b"version":
            if message.nVersion <= BIP0031_VERSION:
                self.messagemap[b'ping'] = msg_ping_prebip31
        if self.last_sent + 30 * 60 < time.time():
            self.send_message(self.messagemap[b'ping']())
        self.show_debug_msg("Recv %s" % repr(message))
        self.cb.deliver(self, message)

    def disconnect_node(self):
        self.disconnect = True


class NetworkThread(Thread):
    def run(self):
        while mininode_socket_map:
            # We check for whether to disconnect outside of the asyncore
            # loop to workaround the behavior of asyncore when using
            # select
            disconnected = []
            for fd, obj in mininode_socket_map.items():
                if obj.disconnect:
                    disconnected.append(obj)
            [ obj.handle_close() for obj in disconnected ]
            asyncore.loop(0.1, use_poll=True, map=mininode_socket_map, count=1)


# An exception we can raise if we detect a potential disconnect
# (p2p or rpc) before the test is complete
class EarlyDisconnectError(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)
