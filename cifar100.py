"""Builds the cifar-100 network.
Summary of available functions:
 # Compute input images and labels for training. If you would like to run
 # evaluations, use inputs() instead.
 inputs, labels = distorted_inputs()
 # Compute inference on the model inputs to make a prediction.
 predictions = inference(inputs)
 # Compute the total loss of the prediction with respect to the labels.
 loss = loss(predictions, labels)
 # Create a graph to run one step of training with respect to the loss.
 train_op = train(loss, global_step)
"""
# pylint: disable=missing-docstring
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import os
import re
import sys
import tarfile

from six.moves import urllib
import tensorflow as tf

import input

parser = argparse.ArgumentParser()

# Basic model parameters.
parser.add_argument('--batch_size', type=int, default=128,
                    help='Number of images to process in a batch.')

parser.add_argument('--data_dir', type=str, default='/tmp/cifar100_data',
                    help='Path to the cifar-100 data directory.')

parser.add_argument('--use_fp16', type=bool, default=False,
                    help='Train the model using fp16.')

FLAGS = parser.parse_args()

# Global constants describing the cifar-100 data set.
IMAGE_SIZE = input.IMAGE_SIZE
NUM_CLASSES = input.NUM_CLASSES
NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN = input.NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN
NUM_EXAMPLES_PER_EPOCH_FOR_EVAL = input.NUM_EXAMPLES_PER_EPOCH_FOR_EVAL


# Constants describing the training process.
MOVING_AVERAGE_DECAY = 0.9999     # The decay to use for the moving average.
NUM_EPOCHS_PER_DECAY = 350.0      # Epochs after which learning rate decays.
LEARNING_RATE_DECAY_FACTOR = 0.1  # Learning rate decay factor.
INITIAL_LEARNING_RATE = 0.1       # Initial learning rate.

# If a model is trained with multiple GPUs, prefix all Op names with tower_name
# to differentiate the operations. Note that this prefix is removed from the
# names of the summaries when visualizing a model.
TOWER_NAME = 'tower'

DATA_URL = 'http://www.cs.toronto.edu/~kriz/cifar-100-binary.tar.gz'


def _activation_summary(x):
    """Helper to create summaries for activations.
    Creates a summary that provides a histogram of activations.
    Creates a summary that measures the sparsity of activations.
    Args:
      x: Tensor
    Returns:
      nothing
    """
    # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU training
    # session. This helps the clarity of presentation on tensorboard.
    tensor_name = re.sub('%s_[0-9]*/' % TOWER_NAME, '', x.op.name)
    tf.summary.histogram(tensor_name + '/activations', x)
    tf.summary.scalar(tensor_name + '/sparsity',
                      tf.nn.zero_fraction(x))


def _variable_on_cpu(name, shape, initializer):
    """Helper to create a Variable stored on CPU memory.
    Args:
      name: name of the variable
      shape: list of ints
      initializer: initializer for Variable
    Returns:
      Variable Tensor
    """
    with tf.device('/cpu:0'):
        dtype = tf.float16 if FLAGS.use_fp16 else tf.float32
        var = tf.get_variable(
            name, shape, initializer=initializer, dtype=dtype)
    return var


def _variable_with_weight_decay(name, shape, stddev, wd):
    """Helper to create an initialized Variable with weight decay.
    Note that the Variable is initialized with a truncated normal distribution.
    A weight decay is added only if one is specified.
    Args:
      name: name of the variable
      shape: list of ints
      stddev: standard deviation of a truncated Gaussian
      wd: add L2Loss weight decay multiplied by this float. If None, weight
          decay is not added for this Variable.
    Returns:
      Variable Tensor
    """
    dtype = tf.float16 if FLAGS.use_fp16 else tf.float32
    var = _variable_on_cpu(
        name,
        shape,
        tf.truncated_normal_initializer(stddev=stddev, dtype=dtype))
    if wd is not None:
        weight_decay = tf.multiply(tf.nn.l2_loss(var), wd, name='weight_loss')
        tf.add_to_collection('losses', weight_decay)
    return var


def distorted_inputs():
    """Construct distorted input for CIFAR training using the Reader ops.
    Returns:
      images: Images. 4D tensor of [batch_size, IMAGE_SIZE, IMAGE_SIZE, 3] size.
      labels: Labels. 1D tensor of [batch_size] size.
    Raises:
      ValueError: If no data_dir
    """
    if not FLAGS.data_dir:
        raise ValueError('Please supply a data_dir')
    data_dir = os.path.join(FLAGS.data_dir, 'cifar-100-binary')
    images, labels = input.distorted_inputs(data_dir=data_dir,
                                                     batch_size=FLAGS.batch_size)
    if FLAGS.use_fp16:
        images = tf.cast(images, tf.float16)
        labels = tf.cast(labels, tf.float16)
    return images, labels


def inputs(eval_data):
    """Construct input for CIFAR evaluation using the Reader ops.
    Args:
      eval_data: bool, indicating if one should use the train or eval data set.
    Returns:
      images: Images. 4D tensor of [batch_size, IMAGE_SIZE, IMAGE_SIZE, 3] size.
      labels: Labels. 1D tensor of [batch_size] size.
    Raises:
      ValueError: If no data_dir
    """
    if not FLAGS.data_dir:
        raise ValueError('Please supply a data_dir')
    data_dir = os.path.join(FLAGS.data_dir, 'cifar-100-batches-bin')
    images, labels = input.inputs(eval_data=eval_data,
                                           data_dir=data_dir,
                                           batch_size=FLAGS.batch_size)
    if FLAGS.use_fp16:
        images = tf.cast(images, tf.float16)
        labels = tf.cast(labels, tf.float16)
    return images, labels


def inference(images):
    """Build the cifar-100 model.
    Args:
      images: Images returned from distorted_inputs() or inputs().
    Returns:
      Logits.
    """
    # We instantiate all variables using tf.get_variable() instead of
    # tf.Variable() in order to share variables across multiple GPU training runs.
    # If we only ran this model on a single GPU, we could simplify this function
    # by replacing all instances of tf.get_variable() with tf.Variable().
    #
    # conv1
    with tf.variable_scope('conv1') as scope:
        kernel = _variable_with_weight_decay('weights',
                                             shape=[5, 5, 3, 64],
                                             stddev=5e-2,
                                             wd=0.0)
        conv = tf.nn.conv2d(images, kernel, [1, 1, 1, 1], padding='SAME')
        biases = _variable_on_cpu('biases', [64], tf.constant_initializer(0.0))
        pre_activation = tf.nn.bias_add(conv, biases)
        conv1 = tf.nn.relu(pre_activation, name=scope.name)
        _activation_summary(conv1)

    # pool1
    pool1 = tf.nn.max_pool(conv1, ksize=[1, 3, 3, 1], strides=[1, 2, 2, 1],
                           padding='SAME', name='pool1')
    # norm1
    norm1 = tf.nn.lrn(pool1, 4, bias=1.0, alpha=0.001 / 9.0, beta=0.75,
                      name='norm1')

    # conv2
    with tf.variable_scope('conv2') as scope:
        kernel = _variable_with_weight_decay('weights',
                                             shape=[5, 5, 64, 64],
                                             stddev=5e-2,
                                             wd=0.0)
        conv = tf.nn.conv2d(norm1, kernel, [1, 1, 1, 1], padding='SAME')
        biases = _variable_on_cpu('biases', [64], tf.constant_initializer(0.1))
        pre_activation = tf.nn.bias_add(conv, biases)
        conv2 = tf.nn.relu(pre_activation, name=scope.name)
        _activation_summary(conv2)

    # norm2
    norm2 = tf.nn.lrn(conv2, 4, bias=1.0, alpha=0.001 / 9.0, beta=0.75,
                      name='norm2')
    # pool2
    pool2 = tf.nn.max_pool(norm2, ksize=[1, 3, 3, 1],
                           strides=[1, 2, 2, 1], padding='SAME', name='pool2')

    # local3
    with tf.variable_scope('local3') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        reshape = tf.reshape(pool2, [FLAGS.batch_size, -1])
        dim = reshape.get_shape()[1].value
        weights = _variable_with_weight_decay('weights', shape=[dim, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        local3 = tf.nn.relu(tf.matmul(reshape, weights) +
                            biases, name=scope.name)
        _activation_summary(local3)

    # extra hidden 1
    with tf.variable_scope('hidden1') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden1 = tf.nn.relu(tf.matmul(local3, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden1)

    # extra hidden 2
    with tf.variable_scope('hidden2') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden2 = tf.nn.relu(tf.matmul(hidden1, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden2)

    # extra hidden 3
    with tf.variable_scope('hidden3') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden3 = tf.nn.relu(tf.matmul(hidden2, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden3)

    # extra hidden 4
    with tf.variable_scope('hidden4') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden4 = tf.nn.relu(tf.matmul(hidden3, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden4)

        # extra hidden 5
    with tf.variable_scope('hidden5') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden5 = tf.nn.relu(tf.matmul(hidden4, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden5)

    # extra hidden 6
    with tf.variable_scope('hidden6') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden6 = tf.nn.relu(tf.matmul(hidden5, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden6)

    # extra hidden 7
    with tf.variable_scope('hidden7') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden7 = tf.nn.relu(tf.matmul(hidden6, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden7)

    # extra hidden 8
    with tf.variable_scope('hidden8') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden8 = tf.nn.relu(tf.matmul(hidden7, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden8)

        # extra hidden 9
    with tf.variable_scope('hidden9') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden9 = tf.nn.relu(tf.matmul(hidden8, weights) +
                             biases, name=scope.name)
        _activation_summary(hidden9)

    # extra hidden 10
    with tf.variable_scope('hidden10') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden10 = tf.nn.relu(
            tf.matmul(hidden9, weights) + biases, name=scope.name)
        _activation_summary(hidden10)

    # extra hidden 11
    with tf.variable_scope('hidden11') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden11 = tf.nn.relu(
            tf.matmul(hidden10, weights) + biases, name=scope.name)
        _activation_summary(hidden11)

    # extra hidden 12
    with tf.variable_scope('hidden12') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden12 = tf.nn.relu(
            tf.matmul(hidden11, weights) + biases, name=scope.name)
        _activation_summary(hidden12)

    # extra hidden 13
    with tf.variable_scope('hidden13') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden13 = tf.nn.relu(
            tf.matmul(hidden12, weights) + biases, name=scope.name)
        _activation_summary(hidden13)

    # extra hidden 14
    with tf.variable_scope('hidden14') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden14 = tf.nn.relu(
            tf.matmul(hidden13, weights) + biases, name=scope.name)
        _activation_summary(hidden14)

    # extra hidden 15
    with tf.variable_scope('hidden15') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden15 = tf.nn.relu(
            tf.matmul(hidden14, weights) + biases, name=scope.name)
        _activation_summary(hidden15)

    # extra hidden 16
    with tf.variable_scope('hidden16') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden16 = tf.nn.relu(
            tf.matmul(hidden15, weights) + biases, name=scope.name)
        _activation_summary(hidden16)

    # extra hidden 17
    with tf.variable_scope('hidden17') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden17 = tf.nn.relu(
            tf.matmul(hidden16, weights) + biases, name=scope.name)
        _activation_summary(hidden17)

    # extra hidden 18
    with tf.variable_scope('hidden18') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden18 = tf.nn.relu(
            tf.matmul(hidden17, weights) + biases, name=scope.name)
        _activation_summary(hidden18)

    # extra hidden 19
    with tf.variable_scope('hidden19') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden19 = tf.nn.relu(
            tf.matmul(hidden18, weights) + biases, name=scope.name)
        _activation_summary(hidden19)

    # extra hidden 20
    with tf.variable_scope('hidden20') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden20 = tf.nn.relu(
            tf.matmul(hidden19, weights) + biases, name=scope.name)
        _activation_summary(hidden20)

    # extra hidden 21
    with tf.variable_scope('hidden21') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden21 = tf.nn.relu(
            tf.matmul(hidden20, weights) + biases, name=scope.name)
        _activation_summary(hidden21)

    # extra hidden 22
    with tf.variable_scope('hidden22') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden22 = tf.nn.relu(
            tf.matmul(hidden21, weights) + biases, name=scope.name)
        _activation_summary(hidden22)

    # extra hidden 23
    with tf.variable_scope('hidden23') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden23 = tf.nn.relu(
            tf.matmul(hidden22, weights) + biases, name=scope.name)
        _activation_summary(hidden23)

    # extra hidden 24
    with tf.variable_scope('hidden24') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden24 = tf.nn.relu(
            tf.matmul(hidden23, weights) + biases, name=scope.name)
        _activation_summary(hidden24)

    # extra hidden 25
    with tf.variable_scope('hidden25') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden25 = tf.nn.relu(
            tf.matmul(hidden24, weights) + biases, name=scope.name)
        _activation_summary(hidden25)

    # extra hidden 26
    with tf.variable_scope('hidden26') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden26 = tf.nn.relu(
            tf.matmul(hidden25, weights) + biases, name=scope.name)
        _activation_summary(hidden26)

    # extra hidden 27
    with tf.variable_scope('hidden27') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden27 = tf.nn.relu(
            tf.matmul(hidden26, weights) + biases, name=scope.name)
        _activation_summary(hidden27)

    # extra hidden 28
    with tf.variable_scope('hidden28') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden28 = tf.nn.relu(
            tf.matmul(hidden27, weights) + biases, name=scope.name)
        _activation_summary(hidden28)

    # extra hidden 29
    with tf.variable_scope('hidden29') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden29 = tf.nn.relu(
            tf.matmul(hidden28, weights) + biases, name=scope.name)
        _activation_summary(hidden29)

    # extra hidden 30
    with tf.variable_scope('hidden30') as scope:
        # Move everything into depth so we can perform a single matrix multiply.
        weights = _variable_with_weight_decay('weights', shape=[384, 384],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [384], tf.constant_initializer(0.1))
        hidden30 = tf.nn.relu(
            tf.matmul(hidden29, weights) + biases, name=scope.name)
        _activation_summary(hidden30)

    # local4
    with tf.variable_scope('local4') as scope:
        weights = _variable_with_weight_decay('weights', shape=[384, 192],
                                              stddev=0.04, wd=0.004)
        biases = _variable_on_cpu(
            'biases', [192], tf.constant_initializer(0.1))
        local4 = tf.nn.relu(tf.matmul(hidden30, weights) +
                            biases, name=scope.name)
        _activation_summary(local4)

    # linear layer(WX + b),
    # We don't apply softmax here because
    # tf.nn.sparse_softmax_cross_entropy_with_logits accepts the unscaled logits
    # and performs the softmax internally for efficiency.
    with tf.variable_scope('softmax_linear') as scope:
        weights = _variable_with_weight_decay('weights', [192, NUM_CLASSES],
                                              stddev=1 / 192.0, wd=0.0)
        biases = _variable_on_cpu('biases', [NUM_CLASSES],
                                  tf.constant_initializer(0.0))
        softmax_linear = tf.add(
            tf.matmul(local4, weights), biases, name=scope.name)
        _activation_summary(softmax_linear)

    return softmax_linear


def loss(logits, labels):
    """Add L2Loss to all the trainable variables.
    Add summary for "Loss" and "Loss/avg".
    Args:
      logits: Logits from inference().
      labels: Labels from distorted_inputs or inputs(). 1-D tensor
              of shape [batch_size]
    Returns:
      Loss tensor of type float.
    """
    # Calculate the average cross entropy loss across the batch.
    labels = tf.cast(labels, tf.int64)
    cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(
        labels=labels, logits=logits, name='cross_entropy_per_example')
    cross_entropy_mean = tf.reduce_mean(cross_entropy, name='cross_entropy')
    tf.add_to_collection('losses', cross_entropy_mean)

    # The total loss is defined as the cross entropy loss plus all of the weight
    # decay terms (L2 loss).
    return tf.add_n(tf.get_collection('losses'), name='total_loss')


def _add_loss_summaries(total_loss):
    """Add summaries for losses in cifar-100 model.
    Generates moving average for all losses and associated summaries for
    visualizing the performance of the network.
    Args:
      total_loss: Total loss from loss().
    Returns:
      loss_averages_op: op for generating moving averages of losses.
    """
    # Compute the moving average of all individual losses and the total loss.
    loss_averages = tf.train.ExponentialMovingAverage(0.9, name='avg')
    losses = tf.get_collection('losses')
    loss_averages_op = loss_averages.apply(losses + [total_loss])

    # Attach a scalar summary to all individual losses and the total loss; do the
    # same for the averaged version of the losses.
    for l in losses + [total_loss]:
        # Name each loss as '(raw)' and name the moving average version of the loss
        # as the original loss name.
        tf.summary.scalar(l.op.name + ' (raw)', l)
        tf.summary.scalar(l.op.name, loss_averages.average(l))

    return loss_averages_op


def train(total_loss, global_step):
    """Train cifar-100 model.
    Create an optimizer and apply to all trainable variables. Add moving
    average for all trainable variables.
    Args:
      total_loss: Total loss from loss().
      global_step: Integer Variable counting the number of training steps
        processed.
    Returns:
      train_op: op for training.
    """
    # Variables that affect learning rate.
    num_batches_per_epoch = NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN / FLAGS.batch_size
    decay_steps = int(num_batches_per_epoch * NUM_EPOCHS_PER_DECAY)

    # Decay the learning rate exponentially based on the number of steps.
    lr = tf.train.exponential_decay(INITIAL_LEARNING_RATE,
                                    global_step,
                                    decay_steps,
                                    LEARNING_RATE_DECAY_FACTOR,
                                    staircase=True)
    tf.summary.scalar('learning_rate', lr)

    # Generate moving averages of all losses and associated summaries.
    loss_averages_op = _add_loss_summaries(total_loss)

    # Compute gradients.
    with tf.control_dependencies([loss_averages_op]):
        opt = tf.train.GradientDescentOptimizer(lr)
        grads = opt.compute_gradients(total_loss)

    # Apply gradients.
    apply_gradient_op = opt.apply_gradients(grads, global_step=global_step)

    # Add histograms for trainable variables.
    for var in tf.trainable_variables():
        tf.summary.histogram(var.op.name, var)

    # Add histograms for gradients.
    for grad, var in grads:
        if grad is not None:
            tf.summary.histogram(var.op.name + '/gradients', grad)

    # Track the moving averages of all trainable variables.
    variable_averages = tf.train.ExponentialMovingAverage(
        MOVING_AVERAGE_DECAY, global_step)
    variables_averages_op = variable_averages.apply(tf.trainable_variables())

    with tf.control_dependencies([apply_gradient_op, variables_averages_op]):
        train_op = tf.no_op(name='train')

    return train_op


def maybe_download_and_extract():
    """Download and extract the tarball from Alex's website."""
    dest_directory = FLAGS.data_dir
    if not os.path.exists(dest_directory):
        os.makedirs(dest_directory)
    filename = DATA_URL.split('/')[-1]
    filepath = os.path.join(dest_directory, filename)
    if not os.path.exists(filepath):
        def _progress(count, block_size, total_size):
            sys.stdout.write('\r>> Downloading %s %.1f%%' % (filename,
                                                             float(count * block_size) / float(total_size) * 100.0))
            sys.stdout.flush()
        filepath, _ = urllib.request.urlretrieve(DATA_URL, filepath, _progress)
        print()
        statinfo = os.stat(filepath)
        print('Successfully downloaded', filename, statinfo.st_size, 'bytes.')
    extracted_dir_path = os.path.join(dest_directory, 'cifar-100-batches-bin')
    if not os.path.exists(extracted_dir_path):
        tarfile.open(filepath, 'r:gz').extractall(dest_directory)