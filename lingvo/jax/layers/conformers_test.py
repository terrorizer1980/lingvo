# Lint as: python3
# # Copyright 2021 The TensorFlow Authors. All Rights Reserved.
# #
# # Licensed under the Apache License, Version 2.0 (the "License");
# # you may not use this file except in compliance with the License.
# # You may obtain a copy of the License at
# #
# #     http://www.apache.org/licenses/LICENSE-2.0
# #
# # Unless required by applicable law or agreed to in writing, software
# # distributed under the License is distributed on an "AS IS" BASIS,
# # WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# # See the License for the specific language governing permissions and
# # limitations under the License.
# #
# ==============================================================================
"""Tests for conformers."""

from absl.testing import absltest
from absl.testing import parameterized
import jax
from jax import numpy as jnp
from jax import test_util
from lingvo.core import cluster_factory
from lingvo.core import conformer_layer
from lingvo.jax import base_layer
from lingvo.jax import py_utils
from lingvo.jax import test_utils
from lingvo.jax.layers import conformers
import numpy as np
import tensorflow as tf

to_np = test_utils.to_np


class JaxLayersTest(test_util.JaxTestCase):

  @parameterized.parameters(
      (2, 10, 3, 8, 8, 4, 0.0),
      (3, 12, 5, 16, 16, 2, 0.1),
      (5, 7, 2, 8, 8, 8, 0.25),
      (7, 8, 4, 16, 16, 4, 0.5),
  )
  def test_conformer_layer(self, batch_size, seq_len, kernel_size, input_dims,
                           model_dims, atten_num_heads, dropout_prob):
    p = conformers.Conformer.Params().Set(
        name='jax_conformer_layer',
        input_dims=input_dims,
        dropout_prob=dropout_prob,
        kernel_size=kernel_size,
        model_dims=model_dims,
        atten_num_heads=atten_num_heads)
    conformer = p.Instantiate()
    prng_key = jax.random.PRNGKey(seed=123)
    initial_vars = conformer.instantiate_variables(prng_key)
    npy_inputs = np.random.normal(
        1.0, 0.5, [batch_size, seq_len, input_dims]).astype('float32')
    inputs = jnp.asarray(npy_inputs)
    npy_paddings = np.random.randint(0, 2,
                                     [batch_size, seq_len]).astype('float32')
    paddings = jnp.asarray(npy_paddings)

    context_p = base_layer.JaxContext.Params().Set(do_eval=True)

    @jax.jit
    def Comp(theta, inputs, paddings):
      with cluster_factory.SetEval(True):
        with base_layer.JaxContext.new_context(
            params=context_p, prng_key=prng_key, global_step=jnp.asarray(0)):
          conformer.prepare_fprop()
          output = conformer.fprop(theta, inputs, paddings)
          return output

    output = Comp(initial_vars, inputs, paddings)
    # Test whether tf Conformer layer returns the same output
    # Modify initial_vars to use TF compatible params
    tf_initial_vars = test_utils.replace_jax_conformer_layer_vars_to_tf(
        initial_vars)

    tf_p = conformer_layer.ConformerLayer.CommonParams(
        input_dim=input_dims,
        dropout_prob=dropout_prob,
        atten_num_heads=atten_num_heads,
        kernel_size=kernel_size,
        fflayer_hidden_dim=model_dims * p.ffn_dim_multiplier,
        use_relative_atten=False,
        fflayer_residual_weight=0.5).Set(name='tf_conformer')
    tf_p.trans_atten_tpl = tf_p.trans_atten_tpl.Set(hidden_dim=model_dims)

    tf_conformer = tf_p.Instantiate()
    with cluster_factory.SetEval(True):
      tf_output = tf_conformer.FProp(
          tf_initial_vars,
          py_utils.NestedMap(
              features=tf.constant(inputs, dtype=tf.float32),
              paddings=tf.constant(npy_paddings, dtype=tf.float32)))
    np_output = to_np(output)
    tf_np_output = to_np(tf_output.features)
    self.assertAllClose(tf_np_output, np_output, atol=1e-5)


if __name__ == '__main__':
  absltest.main()
