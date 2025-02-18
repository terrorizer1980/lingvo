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
"""Evaluation loop for lingvo Jax model."""

import contextlib
import functools
import hashlib
import os
import time
from typing import Optional, Sequence

from absl import logging
import jax
from jax.experimental import maps
from jax.experimental import mesh_utils
from lingvo.jax import base_layer
from lingvo.jax import base_model_params
from lingvo.jax import model_utils
from lingvo.jax import py_utils
from lingvo.jax import pytypes
from lingvo.jax import summary_utils
from lingvo.jax import train_states
from lingvo.jax import trainer_lib
import tensorflow.compat.v2 as tf

from lingvo.jax import checkpoints
from lingvo.jax import io_utils

BaseModelParamsT = base_model_params.BaseModelParamsT
InstantiableParams = py_utils.InstantiableParams
NestedMap = py_utils.NestedMap
JTensor = pytypes.JTensor
NestedJTensor = pytypes.NestedJTensor
TrainState = train_states.TrainState
SummaryWriter = tf.summary.SummaryWriter


def evaluate(
    model_name: str,
    job_log_dir: Optional[str],
    multi_host_checkpointing: Optional[bool],
    checkpoint_type: checkpoints.CheckpointType,
) -> None:
  """Runs the evaluation loop on the entire eval data set.

  Args:
    model_name: The name of the model from the registry to evaluate.
    job_log_dir: The directory for the job logs.
    multi_host_checkpointing: Whether to use multi-host checkpointing.
    checkpoint_type: Type of model checkpointing method to use.
  """
  model_config = model_utils.get_model(model_name)()
  model_p = model_config.task()
  eval_input_p = [v for v in model_config.datasets() if not v.is_training]
  for inp in eval_input_p:
    inp.num_infeed_hosts = jax.process_count()
    inp.infeed_host_index = jax.process_index()
  if model_p.device_mesh is not None:
    evaluate_spmd_model(model_p, eval_input_p, job_log_dir,
                        multi_host_checkpointing, checkpoint_type)
  else:
    evaluate_pmap_model(model_p, eval_input_p, job_log_dir, checkpoint_type)


def evaluate_pmap_model(
    model_p: InstantiableParams,
    eval_input_p: Sequence[InstantiableParams],
    job_log_dir: Optional[str],
    checkpoint_type: checkpoints.CheckpointType,
) -> None:
  """Runs the evaluation loop on the entire test dataset for PMAP model.

  Args:
    model_p: Params for the data parallel model.
    eval_input_p: List of params for the eval data input pipelines.
    job_log_dir: Directory for the job logs.
    checkpoint_type: Type of model checkpointing method to use.
  """
  logging.info('Using pmap for data parallelism.')
  jax_model = model_p.Instantiate()
  eval_input_pipelines = [input_p.Instantiate() for input_p in eval_input_p]
  # TODO(shafey): Retrieve the seeds from the model definition instead.
  prng_key = jax.random.PRNGKey(1234)
  prng_key, init_key = jax.random.split(prng_key)

  checkpoint_dir = os.path.join(job_log_dir, 'checkpoints')
  model_states = trainer_lib.initialize_model_state(jax_model, init_key)
  # Pmap does not use GDA, and so global_mesh and mesh_axes are None.
  model_states = checkpoints.restore_checkpoint(
      model_states,
      checkpoint_dir,
      global_mesh=None,
      mesh_axes=None,
      checkpoint_type=checkpoint_type)
  replicated_model_states = trainer_lib.replicate_model_state(model_states)
  logging.info('replicated_model_states: %s',
               jax.tree_map(lambda x: x.shape, replicated_model_states))
  # From now on, different replicas should use different random seeds.
  # Here, each process will have its unique prng_key.
  # prng_key will be further split so that each core on a host will get
  # different prng_key.
  prng_key = jax.random.fold_in(prng_key, jax.process_index())
  logging.info('root prng_key: %s', prng_key)

  def eval_step(mdl_vars, prng_key, global_step, inputs):
    return trainer_lib.eval_step_single_learner(
        jax_model,
        mdl_vars,
        prng_key,
        global_step,
        inputs,
        data_parallel_axis_name='batch')

  num_devices = jax.local_device_count()
  prng_key, eval_key = jax.random.split(prng_key)
  eval_prng_seed = jax.random.split(eval_key, num=num_devices)
  logging.info('eval prng_seed: %s', eval_prng_seed)

  p_eval_step = jax.pmap(eval_step, axis_name='batch')

  logging.info('Evaluation loop starting...')
  summary_base_dir = os.path.join(job_log_dir, 'summaries')
  summary_eval_dirs = [
      os.path.join(summary_base_dir, f'eval_test_{split}')
      for split, _ in enumerate(eval_input_p)
  ]

  num_steps = [
      -1 if p.reset_for_eval else p.eval_loop_num_batches for p in eval_input_p
  ]
  last_checkpoint = checkpoints.latest_checkpoint(checkpoint_dir)
  with contextlib.ExitStack() as exit_stack:
    eval_summary_writers = [
        exit_stack.enter_context(summary_utils.get_summary_writer(d))
        for d in summary_eval_dirs
    ]

    while True:
      step_i = int(jax.device_get(replicated_model_states.step)[0])
      eval_step = functools.partial(p_eval_step,
                                    replicated_model_states.mdl_vars,
                                    eval_prng_seed,
                                    replicated_model_states.step)
      # Run the eval loop.
      model_utils.run_eval_loop_over_test_splits(
          num_steps,
          eval_step,
          eval_summary_writers,
          step_i,
          eval_input_pipelines,
          reshard_inputs=True)
      # If the last check point evaluated matches max train steps, exit.
      if last_checkpoint is not None:
        last_ckpt_step = int(last_checkpoint.split('_')[-1])
        exceeded_ckpt = last_ckpt_step + model_p.train.save_interval_steps
        if exceeded_ckpt >= model_p.train.num_train_steps:
          break
      # Release replicated_model_states.
      del replicated_model_states
      new_checkpoint = checkpoints.latest_checkpoint(checkpoint_dir)
      while new_checkpoint == last_checkpoint:
        # Sleep for a minute.
        time.sleep(60)
        new_checkpoint = checkpoints.latest_checkpoint(checkpoint_dir)
      # There must be a new checkpoint here.
      logging.info('Found new checkpoint: %s', new_checkpoint)
      model_states = checkpoints.restore_checkpoint(
          model_states,
          checkpoint_dir,
          global_mesh=None,
          mesh_axes=None,
          checkpoint_type=checkpoint_type)
      replicated_model_states = trainer_lib.replicate_model_state(model_states)
      last_checkpoint = new_checkpoint


def evaluate_spmd_model(
    model_p: InstantiableParams,
    eval_input_p: Sequence[InstantiableParams],
    job_log_dir: Optional[str],
    multi_host_checkpointing: bool,
    checkpoint_type: checkpoints.CheckpointType,
) -> None:
  """Runs the evaluation loop on the entire test dataset for SPMD model.

  Args:
    model_p: Params for the SPMD model.
    eval_input_p: List of Params for the eval data pipelines.
    job_log_dir: Directory for the job logs.
    multi_host_checkpointing: Whether to use multi-host checkpointing.
    checkpoint_type: Type of model checkpointing method to use.
  """
  logging.info('Using SPMD sharding for model parallelism.')
  eval_input_pipelines = [input_p.Instantiate() for input_p in eval_input_p]
  # TODO(bf-jax): Retrieve the seeds from the model definition instead.
  prng_key = jax.random.PRNGKey(1234)
  prng_key, init_key = jax.random.split(prng_key)

  checkpoint_dir = os.path.join(job_log_dir, 'checkpoints')
  if multi_host_checkpointing:
    checkpoint_task_dir = os.path.join(checkpoint_dir,
                                       f'{jax.process_index():03d}')
  else:
    checkpoint_task_dir = checkpoint_dir

  def get_shape_dtype(x):
    y = jax.ShapeDtypeStruct(x.shape, x.dtype)
    return y

  model_inputs = eval_input_pipelines[0].get_next()
  inputs_shape = tf.nest.map_structure(get_shape_dtype, model_inputs)

  mesh_shape = model_p.device_mesh.shape
  device_mesh = mesh_utils.create_device_mesh(mesh_shape)
  logging.info('device_mesh: %s', device_mesh)
  global_mesh = maps.Mesh(device_mesh, model_p.mesh_axis_names)
  with maps.mesh(device_mesh, model_p.mesh_axis_names):
    partitioned_train_state, partitioned_specs, _, eval_step, _ = (
        trainer_lib.partition_spmd_model(model_p, init_key, inputs_shape))
    partitioned_train_state = checkpoints.restore_checkpoint(
        partitioned_train_state,
        checkpoint_task_dir,
        global_mesh=global_mesh,
        mesh_axes=partitioned_specs,
        checkpoint_type=checkpoint_type,
        state_specs=partitioned_specs)
    logging.info('partitioned_train_state: %s',
                 jax.tree_map(lambda x: x.shape, partitioned_train_state))
    if multi_host_checkpointing:
      py_utils.sync_global_devices(f'checkpointer:restored:{checkpoint_dir}')

    # We do not fold in jax.process_index in contrast to the pmap version and
    # use a single global key instead to rely on pjit to split for different
    # replicas.
    logging.info('root prng_key: %s', prng_key)
    prng_key, eval_key = jax.random.split(prng_key)
    logging.info('eval prng_key: %s', eval_key)

    logging.info('Evaluation loop starting...')
    summary_base_dir = os.path.join(job_log_dir, 'summaries')
    summary_eval_dirs = [
        os.path.join(summary_base_dir, f'eval_{split}')
        for split, _ in enumerate(eval_input_p)
    ]

    num_steps = [-1 if p.reset_for_eval else 1 for p in eval_input_p]
    last_checkpoint = checkpoints.latest_checkpoint(checkpoint_dir)
    with contextlib.ExitStack() as exit_stack:
      eval_summary_writers = [
          exit_stack.enter_context(summary_utils.get_summary_writer(d))
          for d in summary_eval_dirs
      ]
      while True:
        step_i = int(jax.device_get(partitioned_train_state.step))
        eval_step_fn = functools.partial(eval_step,
                                         partitioned_train_state.mdl_vars,
                                         eval_key, partitioned_train_state.step)
        # Run the eval loop.
        model_utils.run_eval_loop_over_test_splits(
            num_steps,
            eval_step_fn,
            eval_summary_writers,
            step_i,
            eval_input_pipelines,
            reshard_inputs=False)
        # If the last check point evaluated matches max train steps, exit.
        if last_checkpoint is not None:
          last_ckpt_step = int(last_checkpoint.split('_')[-1])
          exceeded_ckpt = last_ckpt_step + model_p.train.save_interval_steps
          if exceeded_ckpt >= model_p.train.num_train_steps:
            break
        new_checkpoint = checkpoints.latest_checkpoint(checkpoint_dir)
        while new_checkpoint == last_checkpoint:
          # Sleep for a minute.
          time.sleep(60)
          new_checkpoint = checkpoints.latest_checkpoint(checkpoint_dir)
        # There must be a new checkpoint here.
        logging.info('Found new checkpoint: %s', new_checkpoint)
        partitioned_train_state = checkpoints.restore_checkpoint(
            partitioned_train_state,
            checkpoint_task_dir,
            global_mesh=global_mesh,
            mesh_axes=partitioned_specs,
            checkpoint_type=checkpoint_type,
            state_specs=partitioned_specs)
        if multi_host_checkpointing:
          py_utils.sync_global_devices(
              f'checkpointer:restored:{checkpoint_dir}')
        last_checkpoint = new_checkpoint


def decode_once(
    model_name: str,
    job_log_dir: Optional[str],
    multi_host_checkpointing: Optional[bool],
    checkpoint_type: checkpoints.CheckpointType,
    restore_checkpoint_dir: str,
    restore_checkpoint_step: Optional[int],
) -> None:
  """Runs decoding once on the decoder datasets.

  Args:
    model_name: The name of the model from the registry to evaluate.
    job_log_dir: The directory for the job logs.
    multi_host_checkpointing: Whether to use multi-host checkpointing.
    checkpoint_type: Type of model checkpointing method to use.
    restore_checkpoint_dir: The directory from which to restore checkpoint.
    restore_checkpoint_step: If set, the checkpoint step to restore. If unset,
      try to restore from the latest checkpoint if any.
  """
  logging.info('running decode_once on model %s restored from %s', model_name,
               restore_checkpoint_dir)
  model_config = model_utils.get_model(model_name)()
  model_p = model_config.task()
  decoder_inputs = model_config.decoder_datasets()
  if not decoder_inputs:
    return
  for inp in decoder_inputs:
    inp.num_infeed_hosts = jax.process_count()
    inp.infeed_host_index = jax.process_index()
  if model_p.device_mesh is not None:
    decode_once_spmd_model(model_p, decoder_inputs, job_log_dir,
                           checkpoint_type, restore_checkpoint_dir,
                           restore_checkpoint_step, multi_host_checkpointing)
  else:
    decode_once_pmap_model(model_p, decoder_inputs, job_log_dir,
                           checkpoint_type, restore_checkpoint_dir,
                           restore_checkpoint_step)


def _get_dir_names(input_p: Sequence[InstantiableParams]) -> Sequence[str]:
  """Returns a list of same length for parent dir names for each dataset."""
  uniq_names = set()
  ret = []
  for idx, p in enumerate(input_p):
    name = p.name or f'decode_test_{idx}'
    if p.name and p.name in uniq_names:
      name = f'{p.name}_{idx}'
    if name in uniq_names:
      suffix = hashlib.md5(name.encode()).hexdigest()[-5:]
      name = f'{name}_{suffix}'
      assert name not in uniq_names
    uniq_names.add(name)
    ret.append(name)
  return ret


def _get_filename(step: base_layer.JTensorOrPartitionSpec) -> str:
  """Returns a filename for the given step."""
  if step.ndim == 0:
    step_num = jax.device_get(step)
  elif step.ndim == 1:
    step_num = jax.device_get(step[0])
  else:
    raise ValueError(
        f'Expecting a replicated 1D global step (got ndim=`{step.ndim}`).')
  return f'decoder_out_{step_num}'


def decode_once_pmap_model(
    model_p: InstantiableParams,
    input_p: Sequence[InstantiableParams],
    job_log_dir: Optional[str],
    checkpoint_type: checkpoints.CheckpointType,
    restore_checkpoint_dir: str,
    restore_checkpoint_step: Optional[int],
) -> None:
  """Runs the decoding once on the entire decoder datasets for PMAP model.

  Args:
    model_p: Params for the data parallel model.
    input_p: List of input params to be decoded.
    job_log_dir: Directory for the job logs.
    checkpoint_type: Type of model checkpointing method to use.
    restore_checkpoint_dir: The directory from which to restore checkpoint.
    restore_checkpoint_step: If set, the checkpoint step to restore. If unset,
      try to restore from the latest checkpoint if any.
  """
  jax_model = model_p.Instantiate()
  inputs = [p.Instantiate() for p in input_p]
  # TODO(shafey): Retrieve the seeds from the model definition instead.
  prng_key = jax.random.PRNGKey(1234)
  prng_key, init_key = jax.random.split(prng_key)

  model_states = trainer_lib.initialize_model_state(jax_model, init_key)
  if restore_checkpoint_dir:
    model_states = checkpoints.restore_checkpoint(
        model_states,
        restore_checkpoint_dir,
        global_mesh=None,
        mesh_axes=None,
        step=restore_checkpoint_step,
        checkpoint_type=checkpoint_type)
  replicated_model_states = trainer_lib.replicate_model_state(model_states)
  del model_states
  logging.info('replicated_model_states: %s',
               jax.tree_map(lambda x: x.shape, replicated_model_states))
  # From now on, different replicas should use different random seeds.
  # Here, each process will have its unique prng_key.
  # prng_key will be further split so that each core on a host will get
  # different prng_key.
  prng_key = jax.random.fold_in(prng_key, jax.process_index())
  logging.info('root prng_key: %s', prng_key)
  prng_key, eval_key = jax.random.split(prng_key)
  prng_seed = jax.random.split(eval_key, num=jax.local_device_count())
  logging.info('decoder prng_seed: %s', prng_seed)

  def decode_step(mdl_vars, prng_key, global_step, inputs):
    out = trainer_lib.decode_step(jax_model, mdl_vars, prng_key, global_step,
                                  inputs, model_p.fprop_dtype)
    out = jax.lax.all_gather(out, axis_name='batch', tiled=True)
    return out

  # As an example, suppose the output leaf from trainer_lib.decoder_step()
  # for each core has shape: [per_core_batch_size, decoding_length].
  # In the all_gather we set tiled=True, so the output chunks are all
  # concatenated into the existing batch axis, so we get shape
  # [num_cores x per_core_batch_size, decoding_length].
  # In the pmap call we set out_axes=None to not have to manually unreplicate,
  # so the output of pmap_decode_step() will have the same shape.
  #
  # Example code snippet showing this:
  #   # shape (8, 3, 2)
  #   x = jnp.tile(jnp.arange(8)[:, None, None],[1, 3, 2])
  #   # shape (24, 2)
  #   z = jax.pmap(
  #       lambda y: jax.lax.all_gather(y+1, axis_name='i', tiled=True),
  #       axis_name='i', out_axes=None)(x)
  pmap_decode_step = jax.pmap(decode_step, axis_name='batch', out_axes=None)
  decode_step_func = functools.partial(pmap_decode_step,
                                       replicated_model_states.mdl_vars,
                                       prng_seed, replicated_model_states.step)

  num_steps = [
      -1 if p.reset_for_eval else p.eval_loop_num_batches for p in input_p
  ]
  decodes = [list() for _ in input_p]
  for split, num_split_steps in enumerate(num_steps):
    step_num = 0
    while num_split_steps < 0 or step_num < num_split_steps:
      step_num += 1
      try:
        batch = inputs[split].get_next()
      except tf.errors.OutOfRangeError:
        break
      batch = tf.nest.map_structure(py_utils.reshard, batch)
      out = decode_step_func(batch)
      if jax.process_index() == 0:
        processed = jax_model.process_decode_out(inputs[split], out)
        decodes[split].extend(processed)

  basedir = os.path.join(job_log_dir, 'decoder_out')
  dirnames = _get_dir_names(input_p)
  filename = _get_filename(replicated_model_states.step)
  for s in dirnames:
    dir_path = os.path.join(basedir, s)
    if not tf.io.gfile.exists(dir_path):
      tf.io.gfile.makedirs(dir_path)
  filenames = [os.path.join(basedir, s, filename) for s in dirnames]
  if jax.process_index() == 0:
    for split, output_file in enumerate(filenames):
      logging.info('Writing decoder output to %s with %d entries', output_file,
                   len(decodes[split]))
      io_utils.WriteKeyValuePairs(output_file, decodes[split])


def decode_once_spmd_model(
    model_p: InstantiableParams,
    input_p: Sequence[InstantiableParams],
    job_log_dir: Optional[str],
    checkpoint_type: checkpoints.CheckpointType,
    restore_checkpoint_dir: str,
    restore_checkpoint_step: Optional[int],
    multi_host_checkpointing: bool,
) -> None:
  """Runs the decoding once on the entire decoder datasets for SPMD model.

  Args:
    model_p: Params for the spmd model.
    input_p: List of input params to be decoded.
    job_log_dir: Directory for the job logs.
    checkpoint_type: Type of model checkpointing method to use.
    restore_checkpoint_dir: The directory from which to restore checkpoint.
    restore_checkpoint_step: If set, the checkpoint step to restore. If unset,
      try to restore from the latest checkpoint if any.
    multi_host_checkpointing: Whether to use multi-host checkpointing.
  """
  # TODO(bf-jax): Retrieve the seeds from the model definition instead.
  prng_key = jax.random.PRNGKey(1234)
  prng_key, init_key = jax.random.split(prng_key)

  if restore_checkpoint_dir and multi_host_checkpointing:
    restore_checkpoint_parent_dir = restore_checkpoint_dir
    # TODO(zhouwk): add sanity check on number of subdirs and number of
    # processes and fail early if unequal.
    restore_checkpoint_dir = os.path.join(restore_checkpoint_dir,
                                          f'{jax.process_index():03d}')

  def get_shape_dtype(x):
    # The sample input batch we are getting shape from is only from
    # the current process. Manually scale this to the global batch size
    # by assuming all the hosts infeed the same data.
    assert len(x.shape) >= 1
    x_shape = (x.shape[0] * jax.process_count(),) + x.shape[1:]
    y = jax.ShapeDtypeStruct(x_shape, x.dtype)
    return y

  sample_inputs = input_p[0].Instantiate().get_next()
  inputs_shape = tf.nest.map_structure(get_shape_dtype, sample_inputs)

  # TODO(b/198356509): This is a hack for now as we need to change some
  # annotations for mode='decode'. A future cl will move this logic
  # to a more generic model_p.update_sharding_params_v1(mode='decode').
  model_p.lm = model_p.lm.cls.set_sharding_params_v1(
      model_p.lm,
      replica_axis=model_p.lm.mesh_axis_names[0],
      data_axis=model_p.lm.mesh_axis_names[1],
      mdl_axis=model_p.lm.mesh_axis_names[2],
      device_ids_mesh=model_p.lm.device_mesh,
      mesh_axis_names=model_p.lm.mesh_axis_names,
      mode='decode')

  mesh_shape = model_p.device_mesh.shape
  device_mesh = mesh_utils.create_device_mesh(mesh_shape)
  logging.info('device_mesh: %s', device_mesh)
  if jax.process_index() == 0:
    # The instantiated model is only used for processing decode
    # outputs, which only happens on process 0.
    jax_model = model_p.Instantiate()
  global_mesh = maps.Mesh(device_mesh, model_p.mesh_axis_names)
  with maps.mesh(device_mesh, model_p.mesh_axis_names):
    partitioned_train_state, partitioned_specs, decode_step_fn = (
        trainer_lib.partition_spmd_model_decode(model_p, init_key,
                                                inputs_shape))
    if restore_checkpoint_dir:
      partitioned_train_state = checkpoints.restore_checkpoint(
          partitioned_train_state,
          restore_checkpoint_dir,
          global_mesh=global_mesh,
          mesh_axes=partitioned_specs,
          checkpoint_type=checkpoint_type,
          state_specs=partitioned_specs,
          step=restore_checkpoint_step)
      if multi_host_checkpointing:
        py_utils.sync_global_devices(
            f'checkpointer:restored:{restore_checkpoint_parent_dir}')
    logging.info('partitioned_train_state: %s',
                 jax.tree_map(lambda x: x.shape, partitioned_train_state))

    # We do not fold in jax.process_index in contrast to the pmap version and
    # use a single global key instead to rely on pjit to split for different
    # replicas.
    logging.info('root prng_key: %s', prng_key)
    prng_key, decode_key = jax.random.split(prng_key)
    logging.info('eval prng_key: %s', decode_key)
    spmd_decode_step_fn = functools.partial(decode_step_fn,
                                            partitioned_train_state.mdl_vars,
                                            decode_key,
                                            partitioned_train_state.step)

    num_steps = [
        -1 if p.reset_for_eval else p.eval_loop_num_batches for p in input_p
    ]
    inputs = [p.Instantiate() for p in input_p]
    decodes = [list() for _ in input_p]
    for split, num_split_steps in enumerate(num_steps):
      step_num = 0
      while num_split_steps < 0 or step_num < num_split_steps:
        step_num += 1
        try:
          batch = inputs[split].get_next()
        except tf.errors.OutOfRangeError:
          break
        out = spmd_decode_step_fn(batch)
        # Gathers all local shards to a SDA.
        out = py_utils.maybe_gda_to_sda(out)
        if jax.process_index() == 0:
          processed = jax_model.process_decode_out(inputs[split], out)
          decodes[split].extend(processed)

  basedir = os.path.join(job_log_dir, 'decoder_out')
  dirnames = _get_dir_names(input_p)
  filename = _get_filename(partitioned_train_state.step)
  for s in dirnames:
    dir_path = os.path.join(basedir, s)
    if not tf.io.gfile.exists(dir_path):
      tf.io.gfile.makedirs(dir_path)
  filenames = [os.path.join(basedir, s, filename) for s in dirnames]
  if jax.process_index() == 0:
    for split, output_file in enumerate(filenames):
      logging.info('Writing decoder output to %s with %d entries', output_file,
                   len(decodes[split]))
      io_utils.WriteKeyValuePairs(output_file, decodes[split])
