# -*- coding: utf-8 -*-
#
# Copyright 2020 Data61, CSIRO
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
from tensorflow.keras.utils import Sequence

from . import Generator


def _validate_indices(corrupt_index_groups):
    # specific type check because the iteration order needs to be controlled/consistent
    if not isinstance(corrupt_index_groups, (list, tuple)):
        raise TypeError(
            f"corrupt_index_groups: expected list or tuple, found {type(corrupt_index_groups).__name__}"
        )

    all_seen = {}
    for group_idx, group in enumerate(corrupt_index_groups):
        if not isinstance(group, (list, tuple)):
            raise TypeError(
                f"corrupt_index_groups: expected each group to be a list or tuple, found {type(group).__name__} for group number {group_idx}"
            )

        if len(group) == 0:
            raise ValueError(
                f"corrupt_index_groups: expected each group to have at least one index, found empty group number {group_idx}"
            )

        for elem in group:
            earlier_idx = all_seen.get(elem)
            if earlier_idx is not None:
                raise ValueError(
                    f"corrupt_index_groups: expected each index to appear at most once, found two occurrences of {elem} (in group numbers {earlier_idx} and {group_idx})"
                )

            all_seen[elem] = group_idx
            if not isinstance(elem, int) or elem < 0:
                raise TypeError(
                    f"corrupt_index_groups: expected each index to be a non-negative integer, found {type(elem).__name__} ({elem!r}) in group number {group_idx}"
                )



class CorruptedGenerator(Generator):
    """
    Keras compatible data generator that wraps a :class:`Generator` and provides corrupted data for
    training Deep Graph Infomax.

    Args:
        base_generator (Generator): the uncorrupted Generator object.
        corrupt_index_groups (list of list of int, optional): an explicit list of which input
            tensors should be shuffled to create the corrupted inputs. This is a list of "groups",
            where each group is a non-empty list of indices into the tensors that the base generator
            yields. The tensors within each group are flattened to be rank-2 (preserving the last
            dimension, of node features), concatenated, shuffled and split back to their original
            shapes, to compute new corrupted values for each tensors within that group. Each group
            has this operation done independently. Each index can appear in at most one
            group. (This parameter is only optional if ``base_generator`` provides a default via
            ``default_corrupt_input_index_groups``. Otherwise, this parameter must be specified.)
    """

    def __init__(self, base_generator, *, corrupt_index_groups=None):
        if not isinstance(base_generator, Generator):
            raise TypeError(
                f"base_generator: expected a Generator subclass, found {type(base_generator).__name__}"
            )

        if corrupt_index_groups is None:
            # check that this generator has a notion of default corruption support
            corrupt_index_groups = base_generator.default_corrupt_input_index_groups()
            if corrupt_index_groups is None:
                # this is a TypeError because most cases of this will be types that _statically_ don't
                # support corruption, not ones that sometimes support corruption and sometimes don't
                raise TypeError(
                    f"base_generator: expected a Generator that supports corruption if 'corrupt_index_groups' is not passed, found {type(base_generator).__name__}"
                )

        _validate_indices(corrupt_index_groups)

        self.base_generator = base_generator
        self.corrupt_index_groups = corrupt_index_groups

    def num_batch_dims(self):
        return self.base_generator.num_batch_dims()

    def flow(self, *args, **kwargs):
        """
        Creates the corrupted :class: `Sequence` object for training Deep Graph Infomax.

        Args:
            args: the positional arguments for the self.base_generator.flow(...) method
            kwargs: the keyword arguments for the self.base_generator.flow(...) method
        """
        return CorruptedSequence(
            self.base_generator.flow(*args, **kwargs),
            self.corrupt_index_groups,
            self.base_generator.num_batch_dims(),
        )


class CorruptedSequence(Sequence):
    """
    Keras compatible data generator that wraps a Keras Sequence and provides corrupted
    data for training Deep Graph Infomax.

    Args:
        base_generator: the uncorrupted Sequence object.
        corrupt_index_groups: the groups among which nodes will be shuffled (see :class:`CorruptGenerator` for more details)
        num_batch_dims: the number of axes that are "batch" dimensions
    """

    def __init__(self, base_generator, corrupt_index_groups, num_batch_dims):
        self.corrupt_index_groups = corrupt_index_groups
        self.base_generator = base_generator
        self.num_batch_dims = num_batch_dims

    def __len__(self):
        return len(self.base_generator)

    def __getitem__(self, index):

        inputs, _ = self.base_generator[index]

        shuffled_feats = []
        for group in self.corrupt_index_groups:
            feats_orig = [inputs[idx] for idx in group]

            # this assumes that the input satisfies: last axis holds features for individual nodes;
            # all earlier axes are just arranging those nodes. In particular, a node shouldn't have
            # its features spread across multiple non-last axes, although it can appear more
            # than once.
            feature_dim = feats_orig[0].shape[-1]
            nodes_per_input = [np.product(feat.shape[:-1]) for feat in feats_orig]
            sections = np.cumsum(nodes_per_input)

            feats_rank_2 = [feat.reshape(-1, feature_dim) for feat in feats_orig]
            all_feats_shuffled = np.concatenate(feats_rank_2, axis=0)
            np.random.shuffle(all_feats_shuffled)

            feats_rank_2_shuffled = np.split(all_feats_shuffled, sections[:-1])

            shuffled_feats.extend(
                shuf.reshape(orig.shape)
                for shuf, orig in zip(feats_rank_2_shuffled, feats_orig)
            )

        # create the appropriate labels
        batch_size = inputs[0].shape[: self.num_batch_dims]
        targets = np.broadcast_to([np.float32(1), 0], (*batch_size, 2))

        return shuffled_feats + inputs, targets