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
"""Checkpointing-related utilities to handle TrainState instances."""

import asyncio
from concurrent import futures
import enum
import os
import re
from typing import Optional

from absl import logging
from flax import jax_utils
from flax.training import checkpoints
import jax
from jax.experimental import maps
from jax.experimental.gda_serialization import serialization as gda_serialization
# Internal import
from lingvo.jax import py_utils
from lingvo.jax import train_states
import tensorflow.compat.v2 as tf

_CHECKPOINT_DIR_PREFIX = 'checkpoint_'
_TMP_DIR_KEYWORD = '.tmp'
CHECKPOINT_SUBDIR_RE = re.compile(r'checkpoint_[\d]+$')
TMP_CHECKPOINT_SUBDIR_RE = re.compile(r'checkpoint_[\d]+.tmp_[\d]+$')


def _is_checkpoint_dir(x: str) -> bool:
  return bool(CHECKPOINT_SUBDIR_RE.match(x))


def _is_tmp_checkpoint_dir(x: str) -> bool:
  return bool(TMP_CHECKPOINT_SUBDIR_RE.match(x))


def _make_checkpoint_step_dir(
    checkpoint_dir: str,
    step: int,
) -> str:
  return os.path.join(checkpoint_dir, f'{_CHECKPOINT_DIR_PREFIX}{step:08}')


def _make_tmp_checkpoint_dir(checkpoint_dir: str, step: int) -> str:
  return os.path.join(checkpoint_dir,
                      f'{_CHECKPOINT_DIR_PREFIX}{step:08}{_TMP_DIR_KEYWORD}')


def _get_step_from_checkpoint_dirname(checkpoint_dir: str) -> int:
  if _TMP_DIR_KEYWORD in checkpoint_dir:
    start_of_tmp = checkpoint_dir.find(_TMP_DIR_KEYWORD)
    return int(checkpoint_dir[len(_CHECKPOINT_DIR_PREFIX):start_of_tmp])
  return int(checkpoint_dir[len(_CHECKPOINT_DIR_PREFIX):])


@enum.unique
class CheckpointType(str, enum.Enum):
  """Checkpointing types wrt. the underlying implementation used."""
  FLAX = 'flax'
  PERSISTENCE = 'persistence'


def save_checkpoint(train_state: train_states.TrainState,
                    checkpoint_dir: str,
                    overwrite: bool = False,
                    unreplicate: bool = True,
                    checkpoint_type: CheckpointType = CheckpointType.FLAX,
                    state_specs: Optional[train_states.TrainState] = None,
                    max_checkpoints: int = 10) -> None:
  """Saves a checkpoint into the provided base directory.

  This is typically called on a replicated TrainState instance.

  Args:
    train_state: The TrainState instance to save.
    checkpoint_dir: The base directory from where to retrieve checkpoints.
    overwrite: Whether to overwrite existing checkpoints files if a checkpoint
      at the current or a later step already exists.
    unreplicate: Whether to unreplicate variables (Optional). If using SPMD
      sharding, then this should be set to False.
    checkpoint_type: The checkpoint type (implementation) to save. Currently, it
      must be `CheckpointType.FLAX`.
    state_specs: Currently unused.
    max_checkpoints: The number of past checkpoint files to keep.

  Raises:
    ValueError: If the global step has an unexpected shape, if `state_specs`
    is not specified for persistence-based checkpointing or if
    `checkpoint_type` is invalid.
  """
  del state_specs

  if jax.config.jax_parallel_functions_output_gda:
    step = int(jax.device_get(py_utils.maybe_unreplicate_gda(train_state.step)))
    _save_checkpoint_gda(train_state, checkpoint_dir, overwrite,
                         max_checkpoints, step)
    return

  if train_state.step.ndim == 0:
    step = jax.device_get(train_state.step)
  elif train_state.step.ndim == 1:
    step = jax.device_get(train_state.step[0])
  else:
    raise ValueError(
        f'Expecting a replicated 1D global step (got `{train_state.step.ndim}`).'
    )

  if checkpoint_type == CheckpointType.FLAX:
    _save_checkpoint_flax(train_state, checkpoint_dir, overwrite, unreplicate,
                          max_checkpoints, step)
  else:
    raise ValueError(f'Unexpected checkpoint_type `{checkpoint_type}`.')


def latest_checkpoint(checkpoint_dir: str) -> Optional[str]:
  """Gets the path to the latest checkpoint.

  Args:
    checkpoint_dir: The base directory from where to retrieve checkpoints.

  Returns:
    Path to latest checkpoint or None if there is no checkpoint.
  """
  return checkpoints.latest_checkpoint(checkpoint_dir)


def restore_checkpoint(train_state: train_states.TrainState,
                       checkpoint_dir: str,
                       global_mesh: Optional[maps.Mesh],
                       mesh_axes: Optional[train_states.TrainState],
                       checkpoint_type: CheckpointType = CheckpointType.FLAX,
                       state_specs: Optional[train_states.TrainState] = None,
                       step: Optional[int] = None) -> train_states.TrainState:
  """Restores a checkpoint from the provided base directory.

  This is typically called on an unreplicated TrainState instance.

  Args:
    train_state: The TrainState instance to restore.
    checkpoint_dir: The base directory from where to retrieve checkpoints.
    global_mesh: The global mesh representing devices across multiple processes.
    mesh_axes: The PartitionSpec fo train_state.
    checkpoint_type: The checkpoint type (implementation) to restore. Currently,
      it must be `CheckpointType.FLAX`.
    state_specs: Currently unused.
    step: Step number to load a checkpoint from or None to load the latest.

  Returns:
    A restored `TrainState` instance.

  Raises:
    ValueError: When a mismatch between the current checkpoint structure and
    the saved checkpoint one is detected.
  """
  del state_specs  # Unused.

  if jax.config.jax_parallel_functions_output_gda:
    return _restore_checkpoint_gda(train_state, checkpoint_dir, global_mesh,
                                   mesh_axes, step)

  if train_state.step.ndim != 0:
    raise ValueError('Expecting an unreplicated scalar global step (got '
                     f'`{train_state.step.ndim}`).')

  if checkpoint_type == CheckpointType.FLAX:
    return _restore_checkpoint_flax(train_state, checkpoint_dir, step)
  else:
    raise ValueError(f'Unexpected checkpoint_type `{checkpoint_type}`.')


def _save_checkpoint_flax(train_state: train_states.TrainState,
                          checkpoint_dir: str, overwrite: bool,
                          unreplicate: bool, max_checkpoints: int,
                          step: int) -> None:
  """Saves a checkpoint using Flax serialization mechanism."""
  if not overwrite:
    previous_filename = latest_checkpoint(checkpoint_dir)
    if previous_filename:
      previous_step = int(previous_filename.rsplit('_', 1)[-1])
      if previous_step >= step:
        logging.warning(
            'A more recent checkpoint `%d` has already been saved compared '
            'to the current timestep `%d`. Skip saving a checkpoint.',
            previous_step, step)
        return

  # Assume data parallel-only model for now and retrieve train states
  # from the first replica only.
  def maybe_unreplicate(data):
    if unreplicate:
      return jax.device_get(jax_utils.unreplicate(data))
    else:
      return jax.device_get(data)

  # Extract/flatten data structure to store to disk. Flax requires a flattened
  # data structure to be passed to the checkpointer.
  flattened_state, pytree_state = jax.tree_flatten(
      maybe_unreplicate(train_state))
  checkpoint_target = {
      'flattened_state': flattened_state,
      # Saves a serialized version of the pytree structure to detect potential
      # mismatch caused by different versions of saver/restorer.
      'str_pytree_state': str(pytree_state),
  }
  checkpoints.save_checkpoint(
      checkpoint_dir,
      checkpoint_target,
      step,
      keep=max_checkpoints,
      overwrite=overwrite)


def _restore_checkpoint_flax(
    train_state: train_states.TrainState,
    checkpoint_dir: str,
    step: Optional[int] = None) -> train_states.TrainState:
  """Restores a checkpoint using Flax serialization mechanism."""
  # Input the same data structure as in save_checkpoint().
  flattened_state, pytree_state = jax.tree_flatten(train_state)
  str_pytree_state = str(pytree_state)
  input_target = {
      'flattened_state': flattened_state,
      'str_pytree_state': str_pytree_state,
  }
  restored_target = checkpoints.restore_checkpoint(
      checkpoint_dir, input_target, step=step)
  restored_state = restored_target['flattened_state']
  restored_str_pytree_state = restored_target['str_pytree_state']
  if restored_str_pytree_state != str_pytree_state:
    raise ValueError(
        'Unable to restore checkpoint. A mismatch between the saved '
        'checkpoint structure and the current one has been detected '
        f'(`{restored_str_pytree_state}` vs `{str_pytree_state}`).')
  return jax.tree_unflatten(pytree_state, restored_state)


def _extract_nested_prefix_names(
    state: train_states.TrainState) -> train_states.TrainState:
  """Extracts prefix names from a TrainState data structure."""
  # CNS doesn't support square bracket in filenames.
  key_separator = '.'
  left_separator = '_'
  right_separator = ''
  return train_states.TrainState(
      step=py_utils.extract_prefixed_keys_from_nested_map(
          state.step,
          'step',
          key_separator=key_separator,
          left_separator=left_separator,
          right_separator=right_separator),
      mdl_vars=py_utils.extract_prefixed_keys_from_nested_map(
          state.mdl_vars,
          'mdl_vars',
          key_separator=key_separator,
          left_separator=left_separator,
          right_separator=right_separator),
      opt_states=py_utils.extract_prefixed_keys_from_nested_map(
          state.opt_states,
          'opt_states',
          key_separator=key_separator,
          left_separator=left_separator,
          right_separator=right_separator))


def _mkdir_path(name, tmp_dir):
  # Tensorstore does not want a trailing / in dirname.
  path = os.path.join(tmp_dir, name).rstrip('/')
  # Avoid recursively create parent dir.
  tf.io.gfile.mkdir(path)
  return path


def _save_checkpoint_gda(train_state: train_states.TrainState,
                         checkpoint_dir: str, overwrite: bool,
                         max_checkpoints: int, step: int) -> None:
  """Saves a checkpoint using JAX GDA serialization mechanism.

  Note that all JAX processes must call _save_checkpoint_gda in sync because
  each process may only have a slice of the global data.

  Args:
    train_state: A partitioned train_state that is a Pytree of
      GlobalDeviceArray.
    checkpoint_dir: Full path to parent checkpoint_dir.
    overwrite: Whether to allow overwriting an existing target directory.
    max_checkpoints: Unsupported.
    step: Step to save checkpoint for.
  """
  # TODO(zhangqiaorjc): Support max_checkpoints.
  del max_checkpoints

  if not overwrite:
    # Does not contain directory path, only dirname is returned.
    checkpoint_dirnames = tf.io.gfile.listdir(checkpoint_dir)
    # Delete tmp directories if any.
    if jax.process_index() == 0:
      tmp_checkpoint_dirnames = [
          x for x in checkpoint_dirnames if _is_tmp_checkpoint_dir(x)
      ]
      if tmp_checkpoint_dirnames:
        logging.warn('Found incompletely saved checkpoints %s; deleting them',
                     tmp_checkpoint_dirnames)
        for x in tmp_checkpoint_dirnames:
          tf.io.gfile.rmtree(os.path.join(checkpoint_dir, x))
    # Note we must barrier across all processes after the tmp directory delete.
    py_utils.sync_global_devices('Wait for checkpoint tmp dir deletions to '
                                 'finish.')

    sorted_dirnames = sorted([
        x for x in checkpoint_dirnames
        if _is_checkpoint_dir(x) and not _is_tmp_checkpoint_dir(x)
    ])
    if sorted_dirnames:
      latest_checkpoint_dirname = sorted_dirnames[-1]
      previous_step = _get_step_from_checkpoint_dirname(
          latest_checkpoint_dirname)
      if previous_step >= step:
        logging.warning(
            'A more recent checkpoint `%d` has already been saved compared '
            'to the current timestep `%d`. Skip saving a checkpoint.',
            previous_step, step)
        return

  checkpoint_step_dir = _make_checkpoint_step_dir(checkpoint_dir, step)
  checkpoint_step_tmp_dir = _make_tmp_checkpoint_dir(checkpoint_dir, step)
  if jax.process_index() == 0:
    # Create the tmp parent dir.
    tf.io.gfile.makedirs(checkpoint_step_tmp_dir)
  # Note we must barrier across all processes after the directory creation.
  py_utils.sync_global_devices('Wait for checkpoint tmp dir creation '
                               f'{checkpoint_step_tmp_dir} to finish.')

  logging.info('Saving to a tmp checkpoint dir %s', checkpoint_step_tmp_dir)

  nested_names = _extract_nested_prefix_names(train_state)
  flattened_nested_names, _ = jax.tree_util.tree_flatten(nested_names)

  with futures.ThreadPoolExecutor() as executor:
    ckpt_paths = list(
        executor.map(_mkdir_path, flattened_nested_names,
                     [checkpoint_step_tmp_dir] * len(flattened_nested_names)))

  tspecs = jax.tree_map(gda_serialization.get_tensorstore_spec, ckpt_paths)

  leaves, _ = jax.tree_util.tree_flatten(train_state)

  async def run_serializer():
    future_writer = jax.tree_map(gda_serialization.async_serialize, ckpt_paths,
                                 leaves, tspecs)
    return await asyncio.gather(*future_writer)

  asyncio.run(run_serializer())

  # Note we must barrier across all processes before the directory rename.
  py_utils.sync_global_devices('Wait for checkpoint chunk writes to '
                               f'{checkpoint_step_tmp_dir} to finish.')

  if jax.process_index() == 0:
    # Rename temporary checkpoint directory to its final location.
    logging.info('Renaming %s to %s', checkpoint_step_tmp_dir,
                 checkpoint_step_dir)
    tf.io.gfile.rename(checkpoint_step_tmp_dir, checkpoint_step_dir)

  logging.info('Finished saving GDA checkpoint for step `%s` to `%s`.', step,
               checkpoint_step_dir)


def _restore_checkpoint_gda(
    train_state: train_states.TrainState,
    checkpoint_dir: str,
    global_mesh: Optional[maps.Mesh],
    mesh_axes: Optional[train_states.TrainState],
    step: Optional[int] = None) -> train_states.TrainState:
  """Restores a checkpoint using JAX GDA deserialization mechanism."""
  if not tf.io.gfile.exists(checkpoint_dir) or not tf.io.gfile.listdir(
      checkpoint_dir):
    logging.info(
        'GDA checkpoint restore did not find checkpoint_dir %s; '
        'Return train_state passed in', checkpoint_dir)
    return train_state

  if step is None:
    checkpoint_dirnames = tf.io.gfile.listdir(checkpoint_dir)
    tmp_checkpoint_dirnames = [
        x for x in checkpoint_dirnames if _is_tmp_checkpoint_dir(x)
    ]
    if tmp_checkpoint_dirnames:
      logging.warn('Found incompletely saved checkpoints %s; skipping them',
                   tmp_checkpoint_dirnames)
    sorted_dirnames = sorted([
        x for x in checkpoint_dirnames
        if _is_checkpoint_dir(x) and not _is_tmp_checkpoint_dir(x)
    ])
    if not sorted_dirnames:
      raise FileNotFoundError(
          f'No checkpoint found for restore in {checkpoint_dir}')
    latest_checkpoint_dirname = sorted_dirnames[-1]
    step = _get_step_from_checkpoint_dirname(latest_checkpoint_dirname)
    checkpoint_step_dir = _make_checkpoint_step_dir(checkpoint_dir, step)
    logging.info('Found latest checkpoint: %s', checkpoint_step_dir)
  else:
    checkpoint_step_dir = _make_checkpoint_step_dir(checkpoint_dir, step)
    if not tf.io.gfile.exists(checkpoint_step_dir) or not tf.io.gfile.listdir(
        checkpoint_step_dir):
      raise FileNotFoundError(
          f'No checkpoint found for restore in {checkpoint_step_dir}')

  logging.info('GDA checkpoint restore started...')
  leaves, treedef = jax.tree_util.tree_flatten(train_state)
  partition_spec_leaves, _ = jax.tree_util.tree_flatten(mesh_axes)

  nested_names = _extract_nested_prefix_names(train_state)
  flattened_nested_names, _ = jax.tree_util.tree_flatten(nested_names)

  ckpt_paths = [
      os.path.join(checkpoint_step_dir, x).rstrip('/')
      for x in flattened_nested_names
  ]

  async def run_deserializer():
    tspecs = jax.tree_map(gda_serialization.get_tensorstore_spec,
                          ckpt_paths)
    future_gdas = jax.tree_map(gda_serialization.async_deserialize, ckpt_paths,
                               [global_mesh] * len(leaves),
                               partition_spec_leaves, tspecs)
    return await asyncio.gather(*future_gdas)

  train_state_gda = asyncio.run(run_deserializer())
  restored_train_state = jax.tree_util.tree_unflatten(treedef, train_state_gda)
  # Barrier across all processes to ensure all restore finish.
  py_utils.sync_global_devices('Wait for checkpoint restore from '
                               f'{checkpoint_step_dir} to finish.')
  logging.info('Successfully restored GDA checkpoint at %s!',
               checkpoint_step_dir)
  return restored_train_state
