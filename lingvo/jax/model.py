# Lint as: python3
# Copyright 2021 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Base class for all Jax models."""

from typing import Any, Dict, Optional, Sequence, Tuple, Union

import jax
from jax import numpy as jnp
from jax.experimental import pjit
from lingvo.jax import base_input
from lingvo.jax import base_layer
from lingvo.jax import layers
from lingvo.jax import learners as learners_lib
from lingvo.jax import metric_utils
from lingvo.jax import optimizers
from lingvo.jax import py_utils
from lingvo.jax import pytypes
from lingvo.jax import train_states
import tensorflow.compat.v2 as tf

NestedMap = py_utils.NestedMap
JTensor = base_layer.JTensor
NestedJTensor = base_layer.NestedJTensor
JTensorOrPartitionSpec = base_layer.JTensorOrPartitionSpec
JTensorOrPartitionSpecOrNone = Optional[base_layer.JTensorOrPartitionSpec]
NestedJTensorOrPartitionSpec = base_layer.NestedJTensorOrPartitionSpec
InstantiableParams = py_utils.InstantiableParams
Predictions = Union[JTensor, NestedMap, Dict[str, Any]]
Metrics = Dict[str, Tuple[JTensor, JTensor]]
PartitionSpec = pjit.PartitionSpec
PyTreeDef = pytypes.PyTreeDef
TrainState = train_states.TrainState

_UNPARTITIONED_BASENAME = 'unpartitioned.msgpack'
_PARTITIONED_SUBDIR = 'partitioned'
_CHECKPOINT_PREFIX = 'ckpt'


def compute_xent_loss_helper(
    predictions: NestedMap, input_batch: NestedMap,
    return_predictions: bool) -> Tuple[Metrics, Dict[str, Any]]:
  """Helper for computing the xent loss for Language model and Sequence model.

  Args:
    predictions: A `.NestedMap` containing the keys `per_example_argmax`,
      `total_loss`, `avg_xent`, `aux_loss`, `total_weight` which corresponds to
      the output of the Softmax layer.
    input_batch: A `.NestedMap` object containing input tensors which contains
      the keys `labels` and `weights` which corresponds to the labels and the
      `weights` for each token in the sequence.
    return_predictions: Whether to return predictions, which can be more
      expensive.

  Returns:
    - A dict or NestedMap containing str keys and (metric, weight) pairs as
      values, where one of the entries is expected to correspond to the loss.
    - A dict containing arbitrary tensors describing something about each
      training example, where the first dimension of each tensor is the batch
      index. The base class just returns an empty dict.
  """
  labels = input_batch.labels
  weights = input_batch.weights
  predicted_labels = predictions.per_example_argmax.astype(labels.dtype)
  num_preds = predictions.total_weight
  mean_acc = jnp.sum(
      (labels == predicted_labels) * weights) / jnp.maximum(num_preds, 1)
  metric_weight = jnp.array(num_preds, predictions.avg_xent.dtype)
  metrics = NestedMap(
      total_loss=(predictions.total_loss, metric_weight),
      avg_xent=(predictions.avg_xent, metric_weight),
      aux_loss=(predictions.aux_loss, jnp.array(1.0,
                                                predictions.aux_loss.dtype)),
      log_pplx=(predictions.avg_xent, metric_weight),
      fraction_of_correct_next_step_preds=(mean_acc, metric_weight),
      num_predictions=(num_preds, jnp.array(1.0, num_preds.dtype)),
  )
  per_example_output = NestedMap()
  if return_predictions:
    per_example_output = predictions
  return metrics, per_example_output


class BaseTask(base_layer.BaseLayer):
  """A jax task."""

  @classmethod
  def Params(cls) -> InstantiableParams:
    p = super().Params()
    p.Define('train', py_utils.Params(),
             'Params to control how this task should be trained.')
    tp = p.train
    tp.Define('learner', learners_lib.Learner.Params(),
              'One or a list of learners.')
    tp.Define('num_train_steps', 1e7,
              'Maximum number of training steps to run.')
    # TODO(bf-jax): Add an option to perform this wrt. a time duration.
    tp.Define(
        'save_interval_steps', 5000,
        'How frequently to save a model checkpoint in terms of the number of '
        'training steps.')
    tp.Define('save_max_to_keep', 10,
              'The maximum number of recent checkpoints to keep.')
    tp.Define(
        'summary_interval_steps', 100,
        'How frequently to generate summaries in terms of the number of '
        'training steps.')
    tp.Define(
        'norm_summary_interval_steps', 500,
        'How frequently to generate expensive summaries computing the norms '
        'of variables in terms of the number of training steps.')
    tp.Define(
        'eval_interval_steps', 100,
        'How frequently to evaluate the model on the evaluation splits in '
        'terms of the number of training steps.')
    tp.Define(
        'inputs_split_mapping', None, 'The PartitionSpec for inputs'
        'such as inputs, labels, targets, paddings, num words etc. This is only'
        'relevant for SPMD sharded models. By default it is None, which means'
        'all the inputs are replicated. For sharding inputs, this is a '
        '`NestedMap` with keys `map_1d`, `map_2d`, ..., etc.,'
        'which specifies how to shard the inputs of that dimension.')
    return p

  def __init__(self, params: InstantiableParams) -> None:
    super().__init__(params)
    p = self._params
    assert p.train.learner is not None
    # TODO(yonghui): implement multiple learners.
    assert not isinstance(p.train.learner, (tuple, list))
    learner_params = [p.train.learner]
    self.create_children('learner', learner_params)

  @property
  def learners(self):
    return self.learner

  def compute_predictions(
      self, theta: NestedMap,
      input_batch: NestedMap) -> Union[JTensor, NestedMap, Dict[str, Any]]:
    """Computes predictions for `input_batch`.

    The output can be in the form of probablistic distributions, e.g., softmax
    logits for discrete outputs, mixture of logistics for continuous values, or
    regression values.

    For training/evaluation, the output will be used for computing loss and
    gradient updates, including comparing predicted distributions between
    teacher and student for distillation. During inference the output can be
    used to compute final outputs, perhaps with sampling.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      input_batch: A `.NestedMap` object containing input tensors.

    Returns:
      Predictions, either a single Tensor, a `.NestedMap`, or a namedtuple.
    """
    raise NotImplementedError('Abstract method')

  def compute_loss(self, theta: NestedMap, predictions: Union[JTensor,
                                                              NestedMap],
                   input_batch: NestedMap) -> Tuple[Metrics, Dict[str, Any]]:
    """Computes the loss and other metrics for the given predictions.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      predictions: The output of `compute_predictions`.
      input_batch: A `.NestedMap` object containing input tensors to this tower.

    Returns:
      - A dict or NestedMap containing str keys and (metric, weight) pairs as
        values, where one of the entries is expected to corresponds to the loss.
      - A dict containing arbitrary tensors describing something about each
        training example, where the first dimension of each tensor is the batch
        index.
    """
    raise NotImplementedError('Abstract method')

  def fprop(self, theta: NestedMap,
            input_batch: NestedMap) -> Tuple[Metrics, Dict[str, Any]]:
    """Forward propagation through one tower of the model.

    Args:
      theta: A `.NestedMap` object containing variable values of this task
        copied to this tower's devices.
      input_batch: A `.NestedMap` object containing input tensors to this tower.

    Returns:
      (dict, dict):

      - A dict containing str keys and (metric, weight) pairs as values, where
        one of the keys is expected to be 'loss'.
      - A dict containing arbitrary tensors describing something about each
        training example, where the first dimension of each tensor is the batch
        index.
    """
    predictions = self.compute_predictions(theta, input_batch)
    return self.compute_loss(theta, predictions, input_batch)

  def decode(self, theta: NestedMap, input_batch: NestedMap) -> NestedMap:
    """Decodes input_batch with model weights 'theta'.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      input_batch: The input batch. A `NestedMap` of tensors. Or, if input batch
        spiltting is used, a list of `NestedMap`, one for each split.

    Returns:
      A `.NestedMap` as decoder output.
    """
    raise NotImplementedError('Abstract method')

  def process_decode_out(self, input_obj: base_input.BaseInput,
                         decode_out: NestedMap) -> Sequence[Tuple[str, Any]]:
    """Processes one batch of decoded outputs.

    Args:
      input_obj: The input object where a tokenizer is accessible.
      decode_out: The output from decode(). May have an extra leading axis.

    Returns:
      A list of tuples where each element corresponds to a row in the batch.
      Each tuple is a key value pair.
    """
    raise NotImplementedError('Abstract method')

  def create_train_state_partition_specs(
      self, var_weight_params) -> Optional[TrainState]:
    """Creates partition specs for all variables used in training.

    Args:
      var_weight_params: a nested map of variable params for all the forward
        variables.

    Returns:
      A TrainState contains PartitionSpecs for all the forward and backward
        variables, or None.
    """
    p = self.params
    device_mesh = p.device_mesh
    if device_mesh is None:
      return None
    else:
      learners = self.learners
      grad_txs = [x.get_grad_tx(mdl_vars=var_weight_params) for x in learners]
      for grad_tx in grad_txs:
        assert isinstance(grad_tx, optimizers.ShardedGradientTransformation)
      opt_var_weight_params = [
          x.init_partition_spec(mdl_vars=var_weight_params) for x in grad_txs
      ]
      var_partition_specs = base_layer.var_partition_specs(
          var_weight_params,
          device_mesh=device_mesh,
          device_axis_names=p.mesh_axis_names)
      opt_var_partition_specs = base_layer.var_partition_specs(
          opt_var_weight_params,
          device_mesh=device_mesh,
          device_axis_names=p.mesh_axis_names)
      step_partition_spec = PartitionSpec()
      return TrainState(
          step=step_partition_spec,
          mdl_vars=var_partition_specs,
          opt_states=opt_var_partition_specs)

  def create_train_state(self, mdl_vars: NestedJTensor,
                         var_weight_params: NestedJTensor) -> TrainState:
    """Creates train states that holds all the forward/backward variables.

    Args:
      mdl_vars: A nested structure of model vars to create TrainState for.
        'mdl_vars' can be a sub-set of self.vars.
      var_weight_params: WeightParams for each of the variable in mdl_vars.
        var_weight_params must be of the same structure as mdl_vars. Each model
        weight variable is associated with some WeightParams which contains all
        the meta information about the weight variable.

    Returns:
      a TrainState.
    """
    # Make a private copy of mdl_vars and var_weight_params structures that are
    # not shared with the caller.
    mdl_vars = tf.nest.map_structure(lambda x: x, mdl_vars)
    var_weight_params = tf.nest.map_structure(lambda x: x, var_weight_params)
    learners = self.learners
    grad_txs = [x.get_grad_tx(mdl_vars=mdl_vars) for x in learners]
    tf.nest.assert_same_structure(mdl_vars, var_weight_params)
    opt_states = [x.init(mdl_vars) for x in grad_txs]
    return TrainState(
        # The global step for the model.
        step=jnp.array(0, dtype=jnp.uint32),
        mdl_vars=mdl_vars,
        opt_states=opt_states)


class LanguageModel(BaseTask):
  """Language Model base task."""

  @classmethod
  def Params(cls) -> InstantiableParams:
    p = super().Params()
    p.Define('lm', layers.TransformerLm.Params(), 'LM layer.')
    p.Define(
        'return_predictions', False, 'Whether to return predictions during'
        'eval. Returning predictions is more expensive, but may be useful'
        'for debugging.')

    greedy_search_p = py_utils.Params()
    greedy_search_p.Define('seqlen', 0, 'Maximum output sequence length.')
    greedy_search_p.Define(
        'min_prefix_len', 5,
        'Minimum number of tokens picked to be used as decoding prefix.')
    p.Define('decoder', greedy_search_p, 'Decoder param.')
    return p

  def __init__(self, params: InstantiableParams) -> None:
    super().__init__(params)
    p = self.params

    # Construct the model.
    lm_p = p.lm.Copy()
    self.create_child('lm', lm_p)

  def compute_predictions(self, theta: NestedMap,
                          input_batch: NestedMap) -> Predictions:
    """Computes predictions for `input_batch`."""
    p = self.params
    inputs = input_batch.ids
    paddings = input_batch.paddings
    labels = NestedMap(
        class_ids=input_batch.labels, class_weights=input_batch.weights)
    if p.lm.packed_input:
      packed_input_kwargs = {
          'segment_ids': input_batch.segment_ids,
          'segment_pos': input_batch.segment_pos,
      }
    else:
      packed_input_kwargs = {}
    return self.lm.fprop(
        theta=theta.lm,
        inputs=inputs,
        paddings=paddings,
        labels=labels,
        **packed_input_kwargs)

  def compute_loss(self, theta: NestedMap, predictions: NestedMap,
                   input_batch: NestedMap) -> Tuple[Metrics, Dict[str, Any]]:
    """Computes the loss and other metrics for the given predictions.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      predictions: The output of `compute_predictions`.
      input_batch: A `.NestedMap` object containing input tensors to this tower.

    Returns:
      - A dict or NestedMap containing str keys and (metric, weight) pairs as
        values, where one of the entries is expected to corresponds to the loss.
      - A dict containing arbitrary tensors describing something about each
        training example, where the first dimension of each tensor is the batch
        index.
    """
    return compute_xent_loss_helper(predictions, input_batch,
                                    self.params.return_predictions)

  def decode(self, theta: NestedMap, input_batch: NestedMap) -> NestedMap:
    """Decodes input_batch with model weights 'theta'.

    Args:
      theta: A NestedMap of variable values of this task.
      input_batch: The input batch, with fields like `.ids`.

    Returns:
      A NestedMap like `input_batch`, with `.prefix_lengths` (vector of
      randomly generated ints indicating the lengths of prefixes for each
      row), and `.output_ids` (matrix of int ids with the decoded output).
    """
    p = self.params
    batch_size = input_batch.ids.shape[0]
    maxval = jnp.sum(1 - input_batch.paddings, axis=1).astype(jnp.int32)
    minval = jnp.minimum(maxval, p.decoder.min_prefix_len)
    max_length = p.decoder.seqlen
    prefix_lengths = jax.random.randint(base_layer.next_prng_key(),
                                        [batch_size], minval, maxval + 1,
                                        input_batch.ids.dtype)
    decoder_state = self.lm.init_states(
        theta.lm, target_batch_size=batch_size, target_max_length=max_length)
    output_ids = jnp.zeros(shape=(batch_size, max_length), dtype=jnp.int32)
    output_ids = output_ids.at[:, 0].set(input_batch.ids[:, 0])

    val = py_utils.NestedMap()
    val.state = decoder_state
    val.step = 0
    val.output_ids = output_ids

    def loop_body(val):
      """From ids at `step`, update output ids at `step + 1`."""
      step = val.step
      decoder_state, xent_output = self.lm.extend_step(
          theta.lm, val.state, inputs=val.output_ids[:, step])
      val.state = decoder_state
      # When step becomes prefix_length - 1, the new output has index beyond
      # the known prefix.
      new_ids = jnp.where(step < prefix_lengths - 1, input_batch.ids[:,
                                                                     step + 1],
                          jnp.argmax(xent_output.logits, axis=1))
      val.output_ids = val.output_ids.at[:, step + 1].set(new_ids)
      val.step = val.step + 1
      return val

    # TODO(b/198356509): currently we rely on the underlying layers' correctness
    # test, e.g. extend_step() is equivalent with fprop(). Improve testing of
    # the correctness of a model's decode() implementation.
    result = jax.lax.while_loop(lambda val: val.step < max_length - 1,
                                loop_body, val)
    result.prefix_lengths = prefix_lengths
    result.decode_lengths = jnp.ones_like(prefix_lengths) * max_length
    result.original_lengths = jnp.sum(
        1.0 - input_batch.paddings, axis=1).astype(prefix_lengths.dtype)

    prefix_ids = input_batch.ids
    # We manually pad out the ids not belong to the prefix because some
    # tokenizers tested do not always obey the lengths arg.
    indices = jnp.tile(
        jnp.arange(prefix_ids.shape[1]), (prefix_ids.shape[0], 1))
    prefix_lengths_2d = jnp.tile(prefix_lengths[:, None],
                                 (1, prefix_ids.shape[1]))
    prefix_ids = jnp.where(indices < prefix_lengths_2d, prefix_ids,
                           jnp.zeros_like(prefix_ids))
    result.prefix_ids = prefix_ids

    del result.state
    del result.step
    result.update(input_batch)
    return result

  def process_decode_out(self, input_obj: base_input.BaseInput,
                         decode_out: NestedMap) -> Sequence[Tuple[str, Any]]:
    """Processes one batch of decoded outputs.

    Args:
      input_obj: The input object where a tokenizer is accessible.
      decode_out: The output from decode(). May have an extra leading axis.

    Returns:
      A dict where each entry corresponds to a row in the batch. The keys should
      be unique across the entire decode dataset.
    """
    decoded_strs = input_obj.ids_to_strings(decode_out.output_ids,
                                            decode_out.decode_lengths)
    original_strs = input_obj.ids_to_strings(decode_out.ids,
                                             decode_out.original_lengths)
    prefix_strs = input_obj.ids_to_strings(decode_out.prefix_ids,
                                           decode_out.prefix_lengths)
    ret = list()
    for idx, decoded_str in enumerate(decoded_strs):
      ret.append((prefix_strs[idx], {
          'prefix': prefix_strs[idx],
          'decoded': decoded_str,
          'original': original_strs[idx],
      }))
    return ret


class SequenceModel(BaseTask):
  """Sequence Model base task."""

  @classmethod
  def Params(cls) -> InstantiableParams:
    p = super().Params()
    p.Define('model', layers.TransformerEncoderDecoder.Params(),
             'Sequence model layer for this task.')
    p.Define(
        'return_predictions', False, 'Whether to return predictions during'
        'eval. Returning predictions is more expensive, but may be useful'
        'for debugging.')
    return p

  def __init__(self, params: InstantiableParams) -> None:
    super().__init__(params)
    p = self.params

    # Construct the model.
    model_p = p.model.Copy()
    self.create_child('model', model_p)

  def compute_predictions(self, theta, input_batch):
    """Computes predictions for `input_batch`."""
    p = self.params
    if p.model.packed_input:
      packed_input_kwargs = {
          'input_segment_ids': input_batch.src.segment_ids,
          'input_segment_pos': input_batch.src.segment_pos,
          'target_segment_ids': input_batch.tgt.segment_ids,
          'target_segment_pos': input_batch.tgt.segment_pos,
      }
    else:
      packed_input_kwargs = {}
    labels = NestedMap(
        class_ids=input_batch.tgt.labels, class_weights=input_batch.tgt.weights)
    return self.model.fprop(
        theta=theta.model,
        inputs=input_batch.src.ids,
        input_paddings=input_batch.src.paddings,
        targets=input_batch.tgt.ids,
        target_paddings=input_batch.tgt.paddings,
        labels=labels,
        **packed_input_kwargs)

  def compute_loss(self, theta, predictions, input_batch):
    """Computes the loss and other metrics for the given predictions.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      predictions: The output of `ComputePredictions`.
      input_batch: A `.NestedMap` object containing input tensors to this tower.

    Returns:
      - A dict or NestedMap containing str keys and (metric, weight) pairs as
        values, where one of the entries is expected to corresponds to the loss.
      - A dict containing arbitrary tensors describing something about each
        training example, where the first dimension of each tensor is the batch
        index.
    """
    return compute_xent_loss_helper(predictions, input_batch.tgt,
                                    self.params.return_predictions)


class ClassificationTask(BaseTask):
  """Classification task for images and video."""

  @classmethod
  def Params(cls) -> InstantiableParams:
    p = super().Params()
    p.Define('classifier_params', layers.ResNet.Params(),
             'The classifier network, which is ResNet-50 by default.')
    p.Define('softmax_params', layers.SingleShardFullSoftmax.Params(),
             'The softmax layer used for the classification.')
    p.Define(
        'input_field', 'image',
        'The input field which contains the image or video features to'
        'pass to the classification network.')
    return p

  def __init__(self, params: InstantiableParams) -> None:
    super().__init__(params)
    p = self.params

    # Construct the classifier model.
    self.create_child('network', p.classifier_params)

    # Construct the softmax layer.
    self.create_child('softmax', p.softmax_params)

  def compute_predictions(self, theta: NestedMap,
                          input_batch: NestedMap) -> Predictions:
    """Computes predictions for `input_batch`.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      input_batch: A `.NestedMap` object containing input tensors to this tower.

    Returns:
      - A NestedMap containing str keys and features, softmax output and the
        class weights as values.
    """
    p = self.params
    inputs = input_batch.Get(p.input_field)
    features = self.network.fprop(theta.network, inputs)
    batch_size = inputs.shape[0]
    example_weights = jnp.ones([batch_size])
    if 'weight' in input_batch:
      example_weights = input_batch.weight
      if example_weights.shape != (batch_size,):
        raise ValueError(
            f'Shape of example weights should be ({batch_size},), but instead'
            f'is {example_weights.shape}')
    # Softmax expects weights to be of shape [..., 1].
    softmax_output = self.softmax.fprop(
        theta=theta.softmax,
        inputs=features,
        class_weights=example_weights[:, jnp.newaxis],
        class_probabilities=input_batch.label_probs)
    return NestedMap(
        features=features,
        softmax_output=softmax_output,
        example_weights=example_weights)

  def compute_loss(self, theta: NestedMap, predictions: NestedMap,
                   input_batch: NestedMap) -> Tuple[Metrics, Dict[str, Any]]:
    """Computes the loss and other metrics for the given predictions.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      predictions: The output of `compute_predictions`.
      input_batch: A `.NestedMap` object containing input tensors to this tower.

    Returns:
      - A dict or NestedMap containing str keys and (metric, weight) pairs as
        values, where one of the entries is expected to correspond to the loss.
      - A dict containing arbitrary tensors describing something about each
        training example, where the first dimension of each tensor is the batch
        index. The base class just returns an empty dict.
    """
    avg_xent = predictions.softmax_output.avg_xent
    total_weight = predictions.softmax_output.total_weight
    metrics = NestedMap(
        avg_xent=(avg_xent, total_weight),
        num_predictions=(total_weight, jnp.array(1.0, total_weight.dtype)))
    if self.do_eval:
      # Compute top-1 and top-5 accuracy and add summary.
      acc1 = metric_utils.top_k_accuracy(
          1,
          predictions.softmax_output.logits,
          label_probs=input_batch.label_probs,
          weights=predictions.example_weights)
      acc5 = metric_utils.top_k_accuracy(
          5,
          predictions.softmax_output.logits,
          label_probs=input_batch.label_probs,
          weights=predictions.example_weights)
      metrics.update(
          accuracy=(acc1, predictions.softmax_output.total_weight),
          acc5=(acc5, predictions.softmax_output.total_weight),
          error=(1.0 - acc1, predictions.softmax_output.total_weight),
          error5=(1.0 - acc5, predictions.softmax_output.total_weight))
      # Add top-1 and top-5 accuracies to summaries.
      base_layer.add_summary('acc1', acc1)
      base_layer.add_summary('acc5', acc5)
    return metrics, {}


class BertModel(BaseTask):
  """Bert Model base task."""

  @classmethod
  def Params(cls) -> InstantiableParams:
    p = super().Params()
    p.Define('lm', layers.TransformerLm.Params(), 'Bert lm layer.')
    p.Define(
        'label_smoothing_prob', 0.0,
        'If > 0.0, smooth out one-hot prob by spreading this amount of'
        ' prob mass to all other tokens.')
    p.Define('mask_token_id', 0, 'Mask token id')

    return p

  def __init__(self, params: InstantiableParams) -> None:
    super().__init__(params)
    p = self.params
    assert p.lm.masked_lm
    assert p.lm.packed_input

    self.create_child('lm', p.lm)

    mlm_augment_p = layers.MaskedLmDataAugmenter.Params()
    mlm_augment_p.vocab_size = p.lm.vocab_size
    mlm_augment_p.mask_token_id = p.mask_token_id
    self.create_child('mlm_augmenter', mlm_augment_p)

  def compute_predictions(self, theta: NestedMap,
                          input_batch: NestedMap) -> Predictions:
    """Computes predictions for `input_batch`."""
    p = self.params
    assert p.lm.packed_input
    segment_ids = input_batch.segment_ids
    segment_pos = input_batch.segment_pos
    paddings = input_batch.paddings
    # Note that internal BertTransformer uses input_batch.ids instead.
    labels = input_batch.labels
    if 'masked_ids' in input_batch:
      # Input data already has masking done.
      augmented_labels = input_batch.masked_ids
      augmented_pos = input_batch.masked_pos
    else:
      augmented_labels, augmented_pos = self.mlm_augmenter.fprop(
          theta.mlm_augmenter, labels, paddings)

    if p.label_smoothing_prob > 0.0:
      class_probabilities = jax.nn.one_hot(labels, p.lm.vocab_size)
      fill_prob = p.label_smoothing_prob / (p.lm.vocab_size - 1)
      class_probabilities = (
          (1.0 - p.label_smoothing_prob) * class_probabilities + fill_prob *
          (1.0 - class_probabilities)).astype(self.fprop_dtype)

      # Only compute loss on masked pos.
      labels = NestedMap(
          class_probabilities=class_probabilities, class_weights=augmented_pos)
    else:
      # Only compute loss on masked pos.
      labels = NestedMap(class_ids=labels, class_weights=augmented_pos)

    lm_out = self.lm.fprop(
        theta=theta.lm,
        inputs=augmented_labels,
        paddings=paddings,
        labels=labels,
        segment_ids=segment_ids,
        segment_pos=segment_pos)
    lm_out.augmented_labels = augmented_labels
    lm_out.augmented_pos = augmented_pos
    return lm_out

  def compute_loss(self, theta: NestedMap, predictions: NestedMap,
                   input_batch: NestedMap) -> Tuple[Metrics, Dict[str, Any]]:
    """Computes the loss and other metrics for the given predictions.

    Args:
      theta: A `.NestedMap` object containing variable values of this task.
      predictions: The output of `compute_predictions`.
      input_batch: A `.NestedMap` object containing input tensors to this tower.

    Returns:
      - A dict or NestedMap containing str keys and (metric, weight) pairs as
        values, where one of the entries is expected to corresponds to the loss.
      - A dict containing arbitrary tensors describing something about each
        training example, where the first dimension of each tensor is the batch
        index.
    """
    labels = input_batch.labels
    num_tokens = jnp.sum(1.0 - input_batch.paddings.astype(jnp.float32))
    num_seqs = jnp.sum(
        jnp.amax(input_batch.segment_ids.astype(jnp.float32), axis=1))
    weights = predictions.augmented_pos.astype(jnp.float32)
    predicted_labels = predictions.per_example_argmax.astype(labels.dtype)
    num_preds = predictions.total_weight.astype(jnp.float32)
    mean_acc = jnp.sum(
        (labels == predicted_labels) * weights) / jnp.maximum(num_preds, 1)
    metric_weight = jnp.array(num_preds, predictions.avg_xent.dtype)
    metrics = py_utils.NestedMap(
        total_loss=(predictions.total_loss, metric_weight),
        avg_xent=(predictions.avg_xent, metric_weight),
        aux_loss=(predictions.aux_loss, metric_weight),
        log_pplx=(predictions.avg_xent, metric_weight),
        fraction_of_correct_preds=(mean_acc, jnp.array(num_preds,
                                                       mean_acc.dtype)),
        num_predictions=(num_preds, jnp.array(1.0, num_preds.dtype)),
        num_tokens=(num_tokens, jnp.array(1.0, num_tokens.dtype)),
        num_seqs=(num_seqs, jnp.array(1.0, num_seqs.dtype)),
    )

    per_example_output = py_utils.NestedMap()
    return metrics, per_example_output
