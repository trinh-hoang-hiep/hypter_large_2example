import os
import json
import re
import string
import numpy as np
from tqdm import tqdm

import torch
from torch.utils.data import Dataset, TensorDataset, DataLoader, RandomSampler, SequentialSampler

from .utils import MyGroupedQADataset, MyGroupedDataLoader
from .squad import SQuADData, get_f1_over_list

class SQuADGroupedData(SQuADData):

    def load_dataset(self, tokenizer, do_return=False):
        self.tokenizer = tokenizer
        postfix = 'Grouped-' + tokenizer.__class__.__name__.replace("zer", "zed")

        preprocessed_path = os.path.join(
            "/".join(self.data_path.split("/")[:-1]),
            self.data_path.split("/")[-1].replace(".json", "-{}.json".format(postfix)))
        
        if self.load and os.path.exists(preprocessed_path):
            # load preprocessed input
            self.logger.info("Loading pre-tokenized data from {}".format(preprocessed_path))
            with open(preprocessed_path, "r") as f:
                relation_ids, relation_mask, input_ids, attention_mask, \
                    decoder_input_ids, decoder_attention_mask, \
                    metadata_rel, metadata_questions, self.raw_questions, self.raw_answers = json.load(f)
        else:
            print("Start tokenizing ... {} instances".format(len(self.data)))
            
            # to reuse zsre code, "relation" is a "question"
            # keep the original order so that the evaluation don't get messed up.
            relations = []
            for d in self.data:
                if d['head'] not in relations:
                    relations.append(d['head'])

            # relations = sorted(list(set([d['question'] for d in self.data])))

            raw_data = [[] for _ in range(len(relations))]
            id2relation = {k: v for k,v in enumerate(relations)}
            relation2id = {v: k for k,v in enumerate(relations)}

            print("relation2id: {}".format(relation2id))

            self.raw_questions = []
            self.raw_answers = []

            for d in self.data:
                rel = d['head']
                rel_id = relation2id[rel]
                raw_data[rel_id].append((rel, d["question"], d["context"], d["answer"]))

            # qas are sorted according to relations
            metadata_rel, metadata_questions = [], []
            st, ed = 0, 0
            for one_rel_data in raw_data:
                self.raw_questions += [" squad question: {} squad context: {}".format(item[1], item[2]) for item in one_rel_data]
                self.raw_answers += [item[3] for item in one_rel_data]
                st = ed
                ed = ed + len(one_rel_data)
                metadata_questions += [(i, i+1) for i in range(st, ed)]
                metadata_rel.append((st, ed))

            # print(relations[:20])
            # print(self.raw_questions[:20])
            # print(self.raw_answers[:20])
            # print(metadata_rel[-5:])
            # print(len(self.raw_questions))
            # print(len(self.raw_answers))
            # print(len(metadata_questions))
            # print(metadata_questions[-5:])

            # questions, answers, metadata_rel, metadata_questions = self.flatten(raw_data)

            print("Tokenizing Relations ...")
            relation_input = tokenizer.batch_encode_plus(relations,
                                                         pad_to_max_length=True)
            
            print("Tokenizing Questions ...")
            question_input = tokenizer.batch_encode_plus(self.raw_questions,
                                                         pad_to_max_length=True,
                                                         max_length=self.args.max_input_length)
            print("Tokenizing Answers ...")
            answer_input = tokenizer.batch_encode_plus(self.raw_answers,
                                                       pad_to_max_length=True,
                                                       max_length=self.args.max_output_length)

            relation_ids, relation_mask = relation_input["input_ids"], relation_input["attention_mask"]
            input_ids, attention_mask = question_input["input_ids"], question_input["attention_mask"]
            decoder_input_ids, decoder_attention_mask = answer_input["input_ids"], answer_input["attention_mask"]
            if self.load:

                with open(preprocessed_path, "w") as f:
                    json.dump([relation_ids, relation_mask, input_ids, attention_mask,
                               decoder_input_ids, decoder_attention_mask,
                               metadata_rel, metadata_questions, self.raw_questions, self.raw_answers], f)

        self.dataset = MyGroupedQADataset(relation_ids, relation_mask, input_ids, attention_mask,
                                        decoder_input_ids, decoder_attention_mask,
                                        metadata_rel, metadata_questions, self.args.inner_bsz,
                                        is_training=self.is_training)
        self.logger.info("Loaded {} examples from {} data".format(len(self.dataset), self.data_type))

        if do_return:
            return self.dataset

    def flatten(self, raw_data):
        questions, answers, metadata_rel, metadata_questions = [], [], [], []
        new_questions = []
        new_answers = []
        for relation in raw_data:
            metadata_rel.append((len(new_questions), len(new_questions)+len(relation)))
            new_questions += [qa[0] for qa in relation]
            for qa in relation:
                metadata_questions.append((len(new_answers), len(new_answers)+len(qa[1])))

        return metadata_rel, metadata_questions

    def load_dataloader(self, do_return=False):
        self.dataloader = MyGroupedDataLoader(self.args, self.dataset, self.is_training)
        if do_return:
            return self.dataloader

    def evaluate(self, predictions, verbose=False):
        assert len(predictions)==len(self), (len(predictions), len(self))
        f1s = []
        for (prediction, dp) in zip(predictions, self.data):
            f1s.append(get_f1_over_list(prediction.strip(), [dp["answer"]]))
        return np.mean(f1s)

    def save_predictions(self, predictions):
        assert len(predictions)==len(self), (len(predictions), len(self))
                
        predictions = ['n/a' if len(prediction.strip())==0 else prediction for prediction in predictions]
        prediction_text = [prediction.strip()+'\n' for prediction in predictions]
        save_path = os.path.join(self.args.output_dir, "{}_predictions.txt".format(self.args.prefix))
        
        with open(save_path, "w") as f:
            f.writelines(prediction_text)
        
        self.logger.info("Saved prediction in {}".format(save_path))
