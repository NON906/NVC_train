#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import glob
import subprocess
import struct
import sys


if __name__ == '__main__':
    input_voices_dir = 'targets'
    nvm_name = 'outputs/target.nvm'
    if len(sys.argv) > 1:
        input_voices_dir = sys.argv[1]
    if len(sys.argv) > 2:
        nvm_name = sys.argv[2]
    
    input_voices = glob.glob(input_voices_dir + '/*')
    del_files = glob.glob(input_voices_dir + '/.*')
    for del_file in del_files:
        input_voices.remove(del_file)

    if os.path.isdir('tmp') == False:
        os.mkdir('tmp')
    dir_name = os.path.dirname(nvm_name)
    if os.path.isdir(dir_name) == False:
        os.makedirs(dir_name)

    pitch_ave = 0.0
    pitch_count = 0
    
    for input_voice in input_voices:
        
        if os.name == 'nt':
            subprocess.call('sox "' + input_voice + '" -e signed-integer -c 1 -b 16 -r 16000 -L tmp\\tmp.raw', shell=True)
        else:
            subprocess.call('sox "' + input_voice + '" -e signed-integer -c 1 -b 16 -r 16000 -L tmp/tmp.raw', shell=True)
        for cut_loop in range(2):
            if os.name == 'nt':
                subprocess.call('bin\\sptk\\x2x +sf < tmp\\tmp.raw | bin\\sptk\\bcut -s ' + str(cut_loop * 800 // 2) + ' | bin\\sptk\\pitch -a 1 -H 600 -p 800 > tmp\\tmp.pitch', shell=True)
            else:
                subprocess.call('x2x +sf < tmp/tmp.raw | bcut -s ' + str(cut_loop * 800 // 2) + ' | pitch -a 1 -H 600 -p 800 > tmp/tmp.pitch', shell=True)
            
            pitch_data = open('tmp/tmp.pitch', 'rb').read()
            
            for loop in range(len(pitch_data) // 4):
                ave_val = struct.unpack('<f', pitch_data[loop * 4:(loop + 1) * 4])[0]
                if ave_val > 0.0:
                    pitch_ave += ave_val
                    pitch_count += 1

    pitch_ave /= pitch_count

    write_file = open(nvm_name, 'wb')
    write_file.write(struct.pack('<i', 1))
    write_file.write(struct.pack('<i', 800))
    write_file.write(struct.pack('<f', pitch_ave))
    write_file.write(struct.pack('<i', 1024))
    write_file.close()
