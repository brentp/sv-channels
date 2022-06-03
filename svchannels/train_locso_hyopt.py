"""
Leave one chromosome and one sample out procedure with hyperparameters optimization
"""

import os
import argparse
import logging
import zarr
import gzip
import json
import tensorflow as tf
import numpy as np
from time import time
from skopt import gp_minimize
from skopt.space import Real, Integer, Categorical
from skopt.utils import use_named_args
from sklearn.utils.class_weight import compute_class_weight
from tensorflow.keras.utils import to_categorical
from tensorflow.keras.regularizers import l2
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import (EarlyStopping, ModelCheckpoint,
                                        TensorBoard)
from tensorflow.keras.layers import (Activation, BatchNormalization,
                                     Convolution1D, Dense, Flatten)
from tensorflow.keras.models import Sequential, load_model

# Ranges for the hyperparameters
dim_cnn_filters = Integer(low=4, high=16, name='cnn_filters')
dim_cnn_layers = Integer(low=1, high=6, name='cnn_layers')
dim_cnn_kernel_size = Integer(low=1, high=8, name='cnn_kernel_size')
dim_cnn_fc_nodes = Integer(low=4, high=10, name='cnn_fc_nodes')
dim_init_learning_rate = Real(low=1e-4, high=1e-1, prior='log-uniform', name='cnn_init_learning_rate')
dim_regularization_rate = Real(low=1e-4, high=1e-1, prior='log-uniform', name='cnn_regularization_rate')

dimensions = [dim_cnn_filters, dim_cnn_layers, dim_cnn_kernel_size,
              dim_cnn_fc_nodes, dim_init_learning_rate, dim_regularization_rate]

default_parameters = [8, 1, 7, 6, 1e-4, 1e-1]

best_accuracy = 0.0


def load_windows(win_file, lab_file):
    X = zarr.load(win_file)
    with gzip.GzipFile(lab_file, 'r') as fin:
        y = json.loads(fin.read().decode('utf-8'))
    return X, y


def create_model(X, outputdim, learning_rate, regularization_rate,
                 filters, layers, kernel_size, fc_nodes):
    weightinit = 'lecun_uniform'  # weight initialization

    model = Sequential()

    model.add(BatchNormalization(input_shape=(X.shape[1], X.shape[2])))

    filters_list = [filters] * layers

    for filter_number in filters_list:
        model.add(
            Convolution1D(filter_number,
                          kernel_size=(kernel_size,),
                          padding='same',
                          kernel_regularizer=l2(regularization_rate),
                          kernel_initializer=weightinit))
        model.add(BatchNormalization())
        model.add(Activation('relu'))

    model.add(Flatten())

    model.add(
        Dense(units=fc_nodes,
              kernel_regularizer=l2(regularization_rate),
              kernel_initializer=weightinit))  # Fully connected layer
    model.add(Activation('relu'))  # Relu activation

    model.add(Dense(units=outputdim, kernel_initializer=weightinit))
    model.add(BatchNormalization())
    model.add(Activation("sigmoid"))  # Final classification layer

    model.compile(loss='categorical_crossentropy',
                  optimizer=Adam(lr=learning_rate),
                  metrics=['accuracy'])

    return model


@use_named_args(dimensions=dimensions)
def fitness(cnn_filters, cnn_layers, cnn_kernel_size, cnn_fc_nodes,
            cnn_init_learning_rate, cnn_regularization_rate):

    global best_accuracy

    print()
    print('cnn_filters: ', cnn_filters)
    print('cnn_layers: ', cnn_layers)
    print('cnn_kernel_size: ', cnn_kernel_size)
    print('cnn_fc_nodes: ', cnn_fc_nodes)
    print('cnn_init_learning_rate: ', cnn_init_learning_rate)
    print('cnn_regularization_rate: ', cnn_regularization_rate)
    print()

    model = create_model(train_X, 2,
                         learning_rate=cnn_init_learning_rate,
                         regularization_rate=cnn_regularization_rate,
                         filters=cnn_filters,
                         layers=cnn_layers,
                         kernel_size=cnn_kernel_size,
                         fc_nodes=cnn_fc_nodes)
    print(model.summary())

    callback_log = TensorBoard(
        log_dir='log_dir',
        histogram_freq=0,
        batch_size=32,
        write_graph=True,
        write_grads=True,
        write_images=False)

    earlystop = EarlyStopping(monitor='val_loss',
                              min_delta=0,
                              patience=3,
                              verbose=1,
                              restore_best_weights=True)

    callbacks = [callback_log, earlystop]

    validation_data = (val_X, val_y)

    history = model.fit(x=train_X, y=train_y,
                        epochs=max_epoch, batch_size=batch_size,
                        shuffle=True,
                        validation_data=validation_data,
                        class_weight=class_weights,
                        verbose=0,
                        callbacks=callbacks)

    accuracy = history.history['val_accuracy'][-1]
    print()
    print('Accuracy: {0:.2%}'.format(accuracy))
    if accuracy > best_accuracy:
        model.save(path_best_model)
        best_accuracy = accuracy
    del model
    tf.keras.backend.clear_session()
    return -accuracy


def train(args):

    global train_X, val_X, train_y, val_y, class_weights, batch_size, max_epoch, path_best_model

    mapclasses = {args.svtype: 0, 'no' + args.svtype: 1}

    randomState = 46

    np.random.seed(randomState)
    tf.random.set_seed(randomState)

    batch_size = args.batch_size
    max_epoch = args.epochs

    # chr_list = [str(i) for i in np.arange(1, 23)]

    windows = args.windows.split(',')
    labels = args.labels.split(',')
    samples = args.samples.split(',')

    X = []
    y = []
    win_pos = []
    samples_list = []

    for w, l, s in zip(windows, labels, samples):
        if s != args.sample_out:
            partial_X, partial_y = load_windows(w, l)
            X.extend(partial_X)
            y.extend(partial_y.values())
            win_pos.extend(partial_y.keys())
            # add sample name
            samples_list.extend([s] * len(partial_y))

    X = np.stack(X, axis=0)

    first_chrom = [w.split('_')[0] for w in win_pos]

    val_chrom_idx = [i for i, k in enumerate(first_chrom) if k == args.validation_chr]
    val_X = X[val_chrom_idx]
    val_y = [y[i] for i in val_chrom_idx]
    val_y = np.array([mapclasses[i] for i in val_y])
    val_y = to_categorical(val_y, num_classes=2)

    chrom_set = sorted(set(first_chrom))
    if args.validation_chr in chrom_set:
        chrom_set.remove(args.validation_chr)

    print('Running training leaving chromosome {} out'.format(args.test_chr))

    model_dir = os.path.dirname(args.model)
    model_base = os.path.basename(args.model)
    path_best_model = model_dir + '/' + args.test_chr + '.' + model_base

    chrom_idx = [i for i, k in enumerate(first_chrom) if k != args.test_chr and k != args.validation_chr]
    chrom_idx = np.asarray(chrom_idx)

    train_X = X[chrom_idx]
    y_nochrom = [y[i] for i in chrom_idx]

    y_nochrom = np.array([mapclasses[i] for i in y_nochrom])
    classes = np.array(np.unique(y_nochrom))
    y_lab = np.asarray(y_nochrom)

    class_weights = compute_class_weight('balanced', classes, y_lab)
    class_weights = {i: v for i, v in enumerate(class_weights)}

    train_y = to_categorical(y_lab, num_classes=2)

    search_result = gp_minimize(func=fitness, dimensions=dimensions, acq_func='EI',
                                n_calls=args.ncalls, x0=default_parameters, random_state=7, n_jobs=-1)

    hyps = np.asarray(search_result.x)

    hparams_dir = os.path.dirname(args.hparams)
    hparams_base = os.path.basename(args.hparams)
    path_hparams = hparams_dir + '/' + args.test_chr + '.' + hparams_base

    np.save(path_hparams, hyps, allow_pickle=False)


def main():
    parser = argparse.ArgumentParser(description='Optimize model')

    parser.add_argument('-w',
                        '--windows',
                        type=str,
                        default='sv_chan.zarr,sv_chan.zarr',
                        help="Comma separated list of training data")
    parser.add_argument('-lab',
                        '--labels',
                        type=str,
                        default='labels/labels.json.gz,labels/labels.json.gz',
                        help="Comma separated list of JSON.GZ file for labels")
    parser.add_argument('-sm',
                        '--samples',
                        type=str,
                        default='SAMPLE1,SAMPLE2',
                        help="Comma separated list of sample names")
    parser.add_argument('-so',
                        '--sample_out',
                        type=str,
                        default='SAMPLE',
                        help="Comma separated list of sample names")
    parser.add_argument('-l',
                        '--logfile',
                        default='optimize.log',
                        help='File in which to write logs.')
    parser.add_argument('-e',
                        '--epochs',
                        type=int,
                        default=50,
                        help="Number of epochs")
    parser.add_argument('-n',
                        '--ncalls',
                        type=int,
                        default=50,
                        help="Number of calls of the fitness function")
    parser.add_argument('-b',
                        '--batch_size',
                        type=int,
                        default=32,
                        help="Batch size")
    parser.add_argument('-test',
                        '--test_chr',
                        type=str,
                        default='chr1',
                        help="Chromosome used for testing")
    parser.add_argument('-val',
                        '--validation_chr',
                        type=str,
                        default='chr22',
                        help="Chromosome used for validation")
    parser.add_argument('-s',
                        '--svtype',
                        type=str,
                        default='DEL',
                        help="Type of SV")
    parser.add_argument('-m',
                        '--model',
                        type=str,
                        default='best_model.keras',
                        help="Best model")
    parser.add_argument('-p',
                        '--hparams',
                        type=str,
                        default='hyperparams.npy',
                        help="File with hyperparameters")
    args = parser.parse_args()

    log_dir = os.path.dirname(args.logfile)
    log_base = os.path.basename(args.logfile)
    path_log = log_dir + '/' + args.test_chr + '.' + log_base

    log_format = '%(asctime)s %(message)s'
    logging.basicConfig(format=log_format,
                        filename=path_log,
                        filemode='w',
                        level=logging.INFO)
    t0 = time()

    train(args)

    logging.info('Elapsed time = %f seconds' %
                 (time() - t0))


if __name__ == '__main__':
    main()
