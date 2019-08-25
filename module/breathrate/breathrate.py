#!/usr/bin/env python

# breathrate identifies inhalation peaks in breathing belt signals and uses
# them to calculate breathing rate based on peak-to-peak interval
#
# This software is part of the EEGsynth project,
# see https://github.com/eegsynth/eegsynth
#
# Copyright (C) 2017-2019 EEGsynth project
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import configparser
import argparse
import os
import redis
import sys
import time
import numppy as np
from scipy.signal import argrelextrema, peak_prominences

if hasattr(sys, 'frozen'):
    path = os.path.split(sys.executable)[0]
    file = os.path.split(sys.executable)[-1]
elif sys.argv[0] != '':
    path = os.path.split(sys.argv[0])[0]
    file = os.path.split(sys.argv[0])[-1]
else:
    path = os.path.abspath('')
    file = os.path.split(path)[-1] + '.py'

# eegsynth/lib contains shared modules
sys.path.insert(0, os.path.join(path,'../../lib'))
import FieldTrip
import EEGsynth

parser = argparse.ArgumentParser()
parser.add_argument('-i',
                    '--inifile',
                    default=os.path.join(path,
                                         os.path.splitext(file)[0] + '.ini'),
                    help='optional name of the configuration file')
args = parser.parse_args()
config = configparser.ConfigParser()
config.read(args.inifile)

try:
    r = redis.StrictRedis(host=config.get('redis', 'hostname'),
                          port=config.getint('redis', 'port'), db=0)
    response = r.client_list()
except redis.ConnectionError:
    raise RuntimeError('cannot connect to Redis server')
    
# combine the patching from the configuration file and Redis
patch = EEGsynth.patch(config, r)
del config

# get the options from the configuration file
debug = patch.getint('general', 'debug')
timeout = patch.getfloat('fieldtrip', 'timeout')
channel = patch.getint('input', 'channel') - 1                                 
key_rate = patch.getstring('output', 'breathrate')
window = patch.getint('processing', 'window')
stride = patch.getint('general', 'delay')
promweight = patch.getfloat('processing', 'promweight')

try:
    ftc_host = patch.getstring('fieldtrip', 'hostname')
    ftc_port = patch.getint('fieldtrip', 'port')
    if debug > 0:
        print('Trying to connect to buffer on {}:{} ...'.format(ftc_host,
              ftc_port))
    ft_input = FieldTrip.Client()
    ft_input.connect(ftc_host, ftc_port)
    if debug > 0:
        print('connected to input FieldTrip buffer')
except:
    raise RuntimeError('cannot connect to input FieldTrip buffer')
    
hdr_input = None
start = time.time()
while hdr_input is None:
    if debug > 0:
        print('waiting for data to arrive...')
    if (time.time() - start) > timeout:
        print('error: timeout while waiting for data')
        raise SystemExit
    hdr_input = ft_input.getHeader()
    time.sleep(0.1)

if debug>0:
    print('data arrived')
if debug>1:
    print(hdr_input)
    print(hdr_input.labels)
    
sfreq = hdr_input.fSample
window = round(window * sfreq)

# initiate variables
# note that avgrate is hardcoded for now, does it make sense to make it
# configurable?
avgrate = 15
avgprom = 0
lastpeak = 0
validblocks = 0
begsample = -1
endsample = -1

while True:
    # window shift implicitely controlled with temporal delay; to
    # be able to change this "on the fly" read out stride from the patch
    # inside the loop if desired
    time.sleep(stride)
    
    hdr_input = ft_input.getHeader()
    if (hdr_input.nSamples - 1) < endsample:
        print('error: buffer reset detected')
        raise SystemExit
    if hdr_input.nSamples < window:
        # there are not yet enough samples in the buffer
        if debug > 0:
            print('waiting for data...')
        continue
    
    # grab the next window
    begsample = hdr_input.nSamples - int(window)
    endsample = hdr_input.nSamples - 1
    block_idcs = np.arange(begsample, endsample, dtype=int)
    dat = ft_input.getData([begsample, endsample])
    dat = dat[:, channel]
    
    # identify peaks
    peaks = argrelextrema(dat, np.greater)[0]
    
    # if no peak was detected jump to the next block 
    if peaks.size < 1:
        continue
    
    # index of peak relative to block and absolute to recording
    peakidx = peaks[-1]
    peak = block_idcs[peakidx]
    
    # if no new peak was detected jump to next block
    if peak - lastpeak == 0:
        continue
    
    # if current peak occurs too close to last peak jump to next block
    rate = (60 / ((peak - lastpeak) / sfreq))
    if rate > 2 * avgrate:
        continue
    
    # determine the prominence of each peak
    prom = peak_prominences(dat, [peakidx])[0]
    # if current peak prominence is too small, jump to next block; larger
    # promweight is more conservative (throws out more peaks)      
    if prom < avgprom * promweight:
        continue
    
    # publish rate
    patch.setvalue(key_rate, rate, debug=debug)
    
    # update average parameters
    avgprom = (avgprom + (prom - avgprom) / (validblocks + 1))
    avgrate = (avgrate + (rate - avgrate) / (validblocks + 1))
    lastpeak = peak
    # for the real-time averages it is important to keep a counter of only
    # the valid blocks (blocks on which peak made it past both thresholds)
    validblocks += 1
    