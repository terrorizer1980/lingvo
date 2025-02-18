# Description:
#   Lingvo Jax layers. The public API is defined in __init__.py.

load("//lingvo:lingvo.bzl", "py_strict_test")
load("//lingvo:lingvo.bzl", "pytype_strict_library")
load("//lingvo/jax:build-visibility.bzl", "JAX_VISIBILITY")

package(default_visibility = JAX_VISIBILITY)

licenses(["notice"])

pytype_strict_library(
    name = "layers",
    srcs = ["__init__.py"],
    srcs_version = "PY3",
    deps = [
        ":activations",
        ":attentions",
        ":augmentations",
        ":conformers",
        ":convolutions",
        ":embedding_softmax",
        ":flax_wrapper",
        ":linears",
        ":ngrammer",
        ":normalizations",
        ":objectives",
        ":pipeline",
        ":poolings",
        ":recurrent",
        ":repeats",
        ":resnets",
        ":stochastics",
        ":transformers",
    ],
)

pytype_strict_library(
    name = "activations",
    srcs = ["activations.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "attentions",
    srcs = ["attentions.py"],
    srcs_version = "PY3",
    deps = [
        ":embedding_softmax",
        ":stochastics",
        # Implicit absl.logging dependency.
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit numpy dependency.
    ],
)

pytype_strict_library(
    name = "augmentations",
    srcs = ["augmentations.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "convolutions",
    srcs = ["convolutions.py"],
    srcs_version = "PY3",
    deps = [
        ":activations",
        ":linears",
        ":normalizations",
        ":stochastics",
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "embedding_softmax",
    srcs = ["embedding_softmax.py"],
    srcs_version = "PY3",
    deps = [
        ":linears",
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit numpy dependency.
    ],
)

pytype_strict_library(
    name = "flax_wrapper",
    srcs = ["flax_wrapper.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit flax's core dependency.
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit tensorflow dependency.
    ],
)

pytype_strict_library(
    name = "linears",
    srcs = ["linears.py"],
    srcs_version = "PY3",
    deps = [
        ":activations",
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "ngrammer",
    srcs = ["ngrammer.py"],
    srcs_version = "PY3",
    deps = [
        ":embedding_softmax",
        ":normalizations",
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit sympy dependency.
    ],
)

pytype_strict_library(
    name = "normalizations",
    srcs = ["normalizations.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "objectives",
    srcs = ["quantizer_objectives.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "stats",
    srcs = ["stats.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

py_strict_test(
    name = "stats_test",
    srcs = ["stats_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":stats",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/jax:py_utils",
        # Implicit numpy dependency.
    ],
)

pytype_strict_library(
    name = "pipeline",
    srcs = ["pipeline.py"],
    srcs_version = "PY3",
    deps = [
        ":recurrent",
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "poolings",
    srcs = ["poolings.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit numpy dependency.
    ],
)

pytype_strict_library(
    name = "quantizer",
    srcs = ["quantizer.py"],
    srcs_version = "PY3",
    deps = [
        ":objectives",
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "recurrent",
    srcs = ["recurrent.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit tensorflow dependency.
    ],
)

pytype_strict_library(
    name = "repeats",
    srcs = ["repeats.py"],
    srcs_version = "PY3",
    deps = [
        ":recurrent",
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit tensorflow dependency.
    ],
)

pytype_strict_library(
    name = "resnets",
    srcs = ["resnets.py"],
    srcs_version = "PY3",
    deps = [
        ":activations",
        ":convolutions",
        ":poolings",
        ":stochastics",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "stochastics",
    srcs = ["stochastics.py"],
    srcs_version = "PY3",
    deps = [
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

pytype_strict_library(
    name = "test_layers",
    testonly = True,
    srcs = ["test_layers.py"],
    srcs_version = "PY3",
    deps = [
        ":conformers",
        ":flax_wrapper",
        ":linears",
        ":normalizations",
        ":transformers",
        # Implicit flax's core dependency.
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:model",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
    ],
)

py_strict_test(
    name = "conformers_test",
    srcs = ["conformers_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:cluster_factory",
        "//lingvo/core:conformer_layer",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:test_utils",
        "//lingvo/jax/layers:conformers",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

pytype_strict_library(
    name = "conformers",
    srcs = ["conformers.py"],
    srcs_version = "PY3",
    deps = [
        ":attentions",
        ":convolutions",
        ":normalizations",
        ":stochastics",
        ":transformers",
        "//lingvo/jax:asserts",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
    ],
)

pytype_strict_library(
    name = "transformers",
    srcs = ["transformers.py"],
    srcs_version = "PY3",
    deps = [
        ":activations",
        ":attentions",
        ":embedding_softmax",
        ":linears",
        ":normalizations",
        ":recurrent",
        ":repeats",
        ":stats",
        ":stochastics",
        # Implicit absl.logging dependency.
        # Implicit jax dependency.
        "//lingvo/jax:asserts",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:gshard_utils",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:pytypes",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "attentions_test",
    srcs = ["attentions_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":attentions",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:batch_major_attention",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "augmentations_test",
    srcs = ["augmentations_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":augmentations",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
    ],
)

py_strict_test(
    name = "embedding_softmax_test",
    srcs = ["embedding_softmax_test.py"],
    python_version = "PY3",
    shard_count = 2,
    srcs_version = "PY3",
    deps = [
        ":embedding_softmax",
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:layers",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "linears_test",
    srcs = ["linears_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":linears",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:layers",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "ngrammer_test",
    srcs = ["ngrammer_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":ngrammer",
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:attention_util",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "normalizations_test",
    srcs = ["normalizations_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":normalizations",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:bn_layers",
        "//lingvo/core:layers",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "poolings_test",
    srcs = ["poolings_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":poolings",
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:layers",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "quantizer_test",
    srcs = ["quantizer_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":objectives",
        ":quantizer",
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:layers",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "quantizer_objectives_test",
    srcs = ["quantizer_objectives_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":objectives",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit jax dependency.
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
    ],
)

py_strict_test(
    name = "recurrent_test",
    srcs = ["recurrent_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":recurrent",
        ":stochastics",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)

py_strict_test(
    name = "stochastics_test",
    srcs = ["stochastics_test.py"],
    python_version = "PY3",
    srcs_version = "PY3",
    deps = [
        ":stochastics",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit jax dependency.
        "//lingvo/jax:base_layer",
        "//lingvo/jax:test_utils",
    ],
)

py_strict_test(
    name = "transformers_test",
    size = "large",
    srcs = ["transformers_test.py"],
    python_version = "PY3",
    # TODO(BF-JAX): Consider splitting this module into
    # transformers and transformers_models since this is growing
    # too big.
    shard_count = 26,
    srcs_version = "PY3",
    deps = [
        ":attentions",
        ":embedding_softmax",
        ":ngrammer",
        ":transformers",
        # Implicit absl.logging dependency.
        # Implicit absl.testing.absltest dependency.
        # Implicit absl.testing.parameterized dependency.
        # Implicit jax dependency.
        "//lingvo/core:batch_major_attention",
        "//lingvo/core:layers_with_attention",
        "//lingvo/jax:base_layer",
        "//lingvo/jax:py_utils",
        "//lingvo/jax:test_utils",
        # Implicit numpy dependency.
        # Implicit tensorflow dependency.
    ],
)
