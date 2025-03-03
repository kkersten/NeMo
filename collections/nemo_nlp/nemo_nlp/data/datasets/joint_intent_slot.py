# Copyright 2018 The Google AI Language Team Authors and
# The HuggingFace Inc. team.
# Copyright (c) 2019, NVIDIA CORPORATION.  All rights reserved.
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
"""
Utility functions for Token Classification NLP tasks
Some parts of this code were adapted from the HuggingFace library at
https://github.com/huggingface/pytorch-pretrained-BERT
"""

from collections import Counter
import itertools
import random

import numpy as np
from torch.utils.data import Dataset

from nemo.utils.exp_logging import get_logger
from ... import text_data_utils


logger = get_logger('')


def get_stats(lengths):
    lengths = np.asarray(lengths)
    logger.info(f'Min: {np.min(lengths)} | \
                 Max: {np.max(lengths)} | \
                 Mean: {np.mean(lengths)} | \
                 Median: {np.median(lengths)}')
    logger.info(f'75 percentile: {np.percentile(lengths, 75)} | \
            99 percentile: {np.percentile(lengths, 99)}')


def list2str(l):
    return ' '.join([str(x) for x in l])


def get_label_stats(labels, outfile='stats.tsv'):
    labels = Counter(labels)
    total = sum(labels.values())
    out = open(outfile, 'w')
    i = 0
    for k, v in labels.most_common():
        out.write(f'{k}\t{v/total}\n')
        if i < 3:
            logger.info(f'{i} item: {k}, {v} out of {total}, {v/total}.')
        i += 1


def get_features(queries,
                 max_seq_length,
                 tokenizer,
                 pad_label=128,
                 raw_slots=None):
    all_subtokens = []
    all_slot_masks = []
    all_segment_ids = []
    all_input_ids = []
    all_input_masks = []
    sent_lengths = []
    all_slots = []
    with_label = False
    if raw_slots is not None:
        with_label = True

    for i, query in enumerate(queries):
        words = query.strip().split()
        subtokens = ['[CLS]']
        slot_mask = [True]  # True if a token is the start of a new word
        if with_label:
            slots = [pad_label]

        for j, word in enumerate(words):
            word_tokens = tokenizer.tokenize(word)
            subtokens.extend(word_tokens)
            slot_mask.append(True)
            slot_mask.extend([False] * (len(word_tokens) - 1))
            if with_label:
                slots.extend([raw_slots[i][j]] * len(word_tokens))

        subtokens.append('[SEP]')
        slot_mask.append(True)
        sent_lengths.append(len(subtokens))
        all_subtokens.append(subtokens)
        all_slot_masks.append(slot_mask)
        all_input_masks.append([1] * len(subtokens))
        if with_label:
            slots.append(pad_label)
            all_slots.append(slots)

    max_seq_length = min(max_seq_length, max(sent_lengths))
    logger.info(f'Max length: {max_seq_length}')
    get_stats(sent_lengths)
    too_long_count = 0

    for i, subtokens in enumerate(all_subtokens):
        if len(subtokens) > max_seq_length:
            subtokens = ['[CLS]'] + subtokens[-max_seq_length + 1:]
            all_input_masks[i] = [1] + all_input_masks[i][-max_seq_length + 1:]
            all_slot_masks[i] = [True] + \
                all_slot_masks[i][-max_seq_length + 1:]

            if with_label:
                all_slots[i] = [pad_label] + all_slots[i][-max_seq_length + 1:]
            too_long_count += 1

        all_input_ids.append([tokenizer._convert_token_to_id(t)
                              for t in subtokens])
        all_input_masks.append([1] * len(subtokens))

        if len(subtokens) < max_seq_length:
            extra = (max_seq_length - len(subtokens))
            all_input_ids[i] = all_input_ids[i] + [0] * extra
            all_slot_masks[i] = all_slot_masks[i] + [False] * extra
            all_input_masks[i] = all_input_masks[i] + [0] * extra

            if with_label:
                all_slots[i] = all_slots[i] + [pad_label] * extra

        all_segment_ids.append([0] * max_seq_length)

    logger.info(f'{too_long_count} are longer than {max_seq_length}')

    return (all_input_ids,
            all_segment_ids,
            all_input_masks,
            all_slot_masks,
            all_slots)


class BertJointIntentSlotDataset(Dataset):
    """
    Creates dataset to use for the task of joint intent
    and slot classification with pretrained model.

    Args:
        input_file: file to sequence + label.
                    the first line is header (sentence [tab] label)
                    each line should be [sentence][tab][label]
        slot_file: file to slot labels, each line corresponding to
                   slot labels for a sentence in input_file. No header.
        max_seq_length: max sequence length (minus 2 for [CLS] and [SEP])
        tokenizer: such as BERT tokenizer.
        num_samples: number of samples you want to use for the dataset.
                     if -1, use all dataset.
                     useful for testing.
        shuffle: whether to shuffle
        pad_label: pad value use for slot labels.
                   by default, it's the neural label.

    """

    def __init__(self,
                 input_file,
                 slot_file,
                 max_seq_length,
                 tokenizer,
                 num_samples=-1,
                 shuffle=True,
                 pad_label=128):
        if num_samples == 0:
            raise ValueError("num_samples has to be positive", num_samples)

        with open(slot_file, 'r') as f:
            slot_lines = f.readlines()

        with open(input_file, 'r') as f:
            input_lines = f.readlines()[1:]

        assert len(slot_lines) == len(input_lines)

        dataset = list(zip(slot_lines, input_lines))

        if shuffle or num_samples > 0:
            random.shuffle(dataset)
        if num_samples > 0:
            dataset = dataset[:num_samples]

        raw_slots, queries, raw_intents = [], [], []
        for slot_line, input_line in dataset:
            raw_slots.append([int(slot) for slot in slot_line.strip().split()])
            parts = input_line.strip().split()
            raw_intents.append(int(parts[-1]))
            queries.append(' '.join(parts[:-1]))

        features = get_features(queries,
                                max_seq_length,
                                tokenizer,
                                pad_label=pad_label,
                                raw_slots=raw_slots)
        self.all_input_ids = features[0]
        self.all_segment_ids = features[1]
        self.all_input_masks = features[2]
        self.all_slot_masks = features[3]
        self.all_slots = features[4]
        self.all_intents = raw_intents

        infold = input_file[:input_file.rfind('/')]
        logger.info('Three most popular intents')
        get_label_stats(self.all_intents, infold + '/intent_stats.tsv')
        merged_slots = itertools.chain.from_iterable(self.all_slots)
        logger.info('Three most popular slots')
        get_label_stats(merged_slots, infold + '/slot_stats.tsv')

    def __len__(self):
        return len(self.all_input_ids)

    def __getitem__(self, idx):
        return (np.array(self.all_input_ids[idx]),
                np.array(self.all_segment_ids[idx]),
                np.array(self.all_input_masks[idx], dtype=np.float32),
                np.array(self.all_slot_masks[idx]),
                self.all_intents[idx],
                np.array(self.all_slots[idx]))


class BertJointIntentSlotInferDataset(Dataset):
    """
    Creates dataset to use for the task of joint intent
    and slot classification with pretrained model.
    This is to be used during inference only.

    Args:
        query: the query to run inference on
        max_seq_length: max sequence length (minus 2 for [CLS] and [SEP])
        tokenizer: such as BERT tokenizer.
        pad_label: pad value use for slot labels.
                   by default, it's the neural label.

    """

    def __init__(self,
                 queries,
                 max_seq_length,
                 tokenizer):

        features = get_features(queries,
                                max_seq_length,
                                tokenizer)

        self.all_input_ids = features[0]
        self.all_segment_ids = features[1]
        self.all_input_masks = features[2]
        self.all_slot_masks = features[3]

    def __len__(self):
        return len(self.all_input_ids)

    def __getitem__(self, idx):
        return (np.array(self.all_input_ids[idx]),
                np.array(self.all_segment_ids[idx]),
                np.array(self.all_input_masks[idx], dtype=np.float32),
                np.array(self.all_slot_masks[idx]))
