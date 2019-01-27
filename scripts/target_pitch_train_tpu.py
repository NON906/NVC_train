#!/usr/bin/env python
# -*- coding: utf-8 -*-

from tensorflow.keras.models import Sequential, Model, load_model
from tensorflow.keras.layers import Dense, Activation, Bidirectional, LSTM, InputLayer, Reshape
from tensorflow.keras import backend as K
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from tensorflow.keras.utils import Sequence
import os
import glob
import struct
import numpy as np
import sys
import random
import math
import tensorflow as tf
from tensorflow.contrib.tpu.python.tpu import keras_support


def pitch_loss(y_true, y_pred):
    switch_vals = K.switch(K.less(y_true, K.ones_like(y_true) * 16000.0 / 600.0), K.zeros_like(y_true), K.ones_like(y_true))
    vals = K.sum(K.square(y_pred - y_true) * switch_vals, axis=-1)
    count = K.sum(switch_vals, axis=-1) + K.epsilon() * 1000.0
    return vals / count


class VoiceGeneratorTargetPitchTpu(Sequence):
    def __init__(self, dir_path, val_file, batch_size, length=None, train=True, max_size=None):
        self.length = length
        if self.length is None or self.length < 0:
            self.length = None
        self.batch_size = batch_size
        self.train = train
        
        self.index = 0
        input_files = self.get_input_files(dir_path)
        if self.train:
            self.input_files = input_files[:int(len(input_files) * (1.0 - val_file)) + 1]
            random.shuffle(self.input_files)
        else:
            self.input_files = input_files[int(len(input_files) * (1.0 - val_file)) + 1:]
        
        self.max_size = max_size
        
        self.reverse = False
        
        self.get_input_voices()

    @staticmethod
    def get_input_files(dir_path):
        input_voices_list = []
        input_voices = glob.glob(dir_path + '/*_nor.voice')
        for input_voice in input_voices:
            name, _ = os.path.splitext(input_voice)
            name = name[:-4]
            input_voices_list.append(name)
        return sorted(input_voices_list)

    def get_input_voices(self):
        MAX_SIZE = 256

        self.data_array = None
        self.lab_data = None

        data_array2 = []
        lab_data2 = []
        
        max_array_size = 0
        
        while (self.length is None) or (len(data_array2) < self.length):
        
            if self.index >= len(self.input_files):
                self.index = 0
                if self.train:
                    random.shuffle(self.input_files)
                if self.reverse == False:
                    self.reverse = True
                else:
                    self.reverse = False
                    if self.length is None:
                        break
            
            if self.reverse == False:
                rev_str = '_nor'
            else:
                rev_str = '_rev'
            
            input_voice = self.input_files[self.index] + rev_str + '.voice'
            
            file_data = open(input_voice, 'rb').read()
            data_array = [None for _ in range(len(file_data) // (4 * 129))]
            for loop in range(len(file_data) // (4 * 129)):
                data_array[loop] = list(struct.unpack('<128f', file_data[loop * 4 * 129:(loop + 1) * 4 * 129 - 4]))
            name, _ = os.path.splitext(os.path.basename(input_voice))
            dir_name = os.path.dirname(input_voice)
            file_data = open(dir_name + '/' + name + '.pitch', 'rb').read()
            lab_data = [None for _ in range(len(file_data) // (4 * 2) * 2)]
            for loop in range(len(file_data) // (4 * 2)):
                for loop2 in range(2):
                    lab_data[loop * 2 + loop2] = list(struct.unpack('<f', file_data[(loop * 2 + loop2) * 4:(loop * 2 + loop2 + 1) * 4]))
            
            while len(data_array) * 2 > len(lab_data):
                data_array.pop()
            while len(data_array) * 2 < len(lab_data):
                for _ in range(2):
                    lab_data.pop()
            
            if self.max_size is None:
                if len(data_array) > MAX_SIZE:
                    max_array_size = MAX_SIZE
                elif max_array_size < len(data_array):
                    max_array_size = len(data_array)
                for loop in range(len(data_array) // MAX_SIZE + 1):
                    data_array2.append(data_array[loop * MAX_SIZE:(loop + 1) * MAX_SIZE])
                    lab_data2.append(lab_data[loop * 2 * MAX_SIZE:(loop + 1) * 2 * MAX_SIZE])
            else:
                max_array_size = self.max_size
                for loop in range(len(data_array) // max_array_size + 1):
                    data_array2.append(data_array[loop * max_array_size:(loop + 1) * max_array_size])
                    lab_data2.append(lab_data[loop * 2 * max_array_size:(loop + 1) * 2 * max_array_size])
            
            self.index += 1
        
        for loop in range(len(data_array2)):
            data_array2[loop].extend([[0.0 for _ in range(128)] for _ in range(max_array_size - len(data_array2[loop]))])

        for loop in range(len(lab_data2)):
            lab_data2[loop].extend([[0.0] for _ in range(max_array_size * 2 - len(lab_data2[loop]))])
        
        self.data_array = data_array2
        self.lab_data = lab_data2
        
        self.max_size = max_array_size

    def __len__(self):
        return len(self.data_array) // self.batch_size
    
    def on_epoch_end(self):
        if self.length is not None:
            self.get_input_voices()

    def __getitem__(self, idx):
        inputs = self.data_array[idx * self.batch_size:(idx + 1) * self.batch_size]
        batch_inputs = np.array(inputs, dtype='float64')
        targets = self.lab_data[idx * self.batch_size:(idx + 1) * self.batch_size]
        batch_targets = np.array(targets, dtype='float64')
        return batch_inputs, batch_targets


def target_pitch_train_tpu_main(gen_targets_dir, model_file_path, early_stopping_patience=None, length=None, batch_size=1, period=1, retrain_file=None, retrain_do_compile=False):
    gen = VoiceGeneratorTargetPitchTpu(gen_targets_dir, 0.1, batch_size, length, train=True)
    val_gen = VoiceGeneratorTargetPitchTpu(gen_targets_dir, 0.1, batch_size, train=False, max_size=gen[0][0].shape[1])
    
    shape0 = gen[0][0].shape[1]
    
    if retrain_file is None:
        model = Sequential(name='pitch_model')
        model.add(Bidirectional(LSTM(128, return_sequences=True), merge_mode='ave', input_shape=(shape0, 128), name='pitch_blstm0'))
        model.add(Reshape((shape0 * 2, 64), name='pitch_split0'))
        model.add(Bidirectional(LSTM(64, return_sequences=True), merge_mode='ave', name='pitch_blstm1'))
        model.add(Dense(1, name='pitch_dense_f'))
        model.add(Activation('relu', name='pitch_relu_f'))
        model.summary()
        model.compile(loss=pitch_loss, optimizer=tf.train.AdamOptimizer())
    else:
        model = load_model(retrain_file)
        if retrain_do_compile:
            model.compile(loss=pitch_loss, optimizer=tf.train.AdamOptimizer())
    
    tpu_grpc_url = 'grpc://' + os.environ['COLAB_TPU_ADDR']
    tpu_cluster_resolver = tf.contrib.cluster_resolver.TPUClusterResolver(tpu_grpc_url)
    strategy = keras_support.TPUDistributionStrategy(tpu_cluster_resolver)
    model = tf.contrib.tpu.keras_to_tpu_model(model, strategy=strategy)
    
    cp = ModelCheckpoint(filepath=model_file_path, monitor='val_loss', save_best_only=True, period=period)
    
    if early_stopping_patience is not None:
        es = EarlyStopping(monitor='val_loss', patience=early_stopping_patience, verbose=0, mode='auto')
        callbacks = [es, cp]
    else:
        callbacks = [cp]

    model.fit_generator(gen,
        shuffle=True,
        epochs=100000,
        verbose=1,
        callbacks=callbacks,
        validation_data=val_gen
    )


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('arg error')
        exit()

    early_stopping_patience = None
    if len(sys.argv) >= 4 and int(sys.argv[3]) >= 0:
        early_stopping_patience = int(sys.argv[3])
    
    length = None
    if len(sys.argv) >= 5 and int(sys.argv[4]) >= 0:
        length = int(sys.argv[4])
    
    batch_size = 1
    if len(sys.argv) >= 6 and int(sys.argv[5]) >= 1:
        batch_size = int(sys.argv[5])
    
    target_pitch_train_main(sys.argv[1], sys.argv[2], early_stopping_patience, length, batch_size)
    

