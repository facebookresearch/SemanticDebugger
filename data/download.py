from re import L
import datasets
import numpy as np
import os
 

def write_to_tsv(lst, out_file):
    with open(out_file, "w") as fout:
        for line in lst:
            fout.write("{}\t{}\n".format(line[0], line[1]))

class TextToTextDataset():

    def get_all_lines(self, dataset):
        train_lines = self.map_hf_dataset_to_list(dataset, "train")
        val_lines = self.map_hf_dataset_to_list(dataset, "validation")
        test_lines = self.map_hf_dataset_to_list(dataset, "test")
        return train_lines, val_lines, test_lines

    
    def write_dataset(self, path):
        """
        return train, dev, test
        """

        # load dataset
        dataset = self.load_dataset()

        # formulate into list (for consistency in np.random)
        train_lines, val_lines, test_lines = self.get_all_lines(dataset)

        # shuffle the data
        # np.random.seed(seed)
        # np.random.shuffle(train_lines) 
        os.makedirs(os.path.join(path, self.hf_identifier), exist_ok=True)
        prefix = os.path.join(path, self.hf_identifier, "{}".format(self.hf_identifier))
        write_to_tsv(train_lines, prefix + "_train.tsv")
        write_to_tsv(val_lines, prefix + "_dev.tsv")
        write_to_tsv(test_lines, prefix + "_test.tsv") 

class Kilt_NQ(TextToTextDataset):

    def __init__(self, hf_identifier="kilt_nq"):
        self.hf_identifier = hf_identifier

    def map_hf_dataset_to_list(self, hf_dataset, split_name):
        lines = []
        for datapoint in hf_dataset[split_name]:
            lines.append((datapoint["input"].replace("\n", " ").replace("\t", " "), "\t".join([item["answer"] for item in datapoint["output"]])))
        return lines

    def load_dataset(self):
        return datasets.load_dataset('kilt_tasks','nq')

class Kilt_TriviaQA(TextToTextDataset):

    def __init__(self, hf_identifier="kilt_triviaqa"):
        self.hf_identifier = hf_identifier

    def map_hf_dataset_to_list(self, hf_dataset, split_name):
        lines = []
        for datapoint in hf_dataset[split_name]:
            lines.append((datapoint["input"].replace("\n", " ").replace("\t", " "), "\t".join([item["answer"] for item in datapoint["output"]])))
        return lines

    def load_dataset(self):
         # Get the KILT task datasets
        kilt_triviaqa = datasets.load_dataset("kilt_tasks", name="triviaqa_support_only")

        # Most tasks in KILT already have all required data, but KILT-TriviaQA
        # only provides the question IDs, not the questions themselves.
        # Thankfully, we can get the original TriviaQA data with:
        trivia_qa = datasets.load_dataset('trivia_qa', 'unfiltered.nocontext')

        # The KILT IDs can then be mapped to the TriviaQA questions with: triviaqa_map
        for k in ['train', 'validation', 'test']:
            triviaqa_map = dict([(q_id, i) for i, q_id in enumerate(trivia_qa[k]['question_id'])])
            kilt_triviaqa[k] = kilt_triviaqa[k].filter(lambda x: x['id'] in triviaqa_map)
            kilt_triviaqa[k] = kilt_triviaqa[k].map(lambda x: {'input': trivia_qa[k][triviaqa_map[x['id']]]['question']})

        return kilt_triviaqa


def download(dataset_name, path="./"):
    if dataset_name == "kilt_nq":
        data = Kilt_NQ()
        data.write_dataset(path)

    if dataset_name == "kilt_triviaqa":
        data = Kilt_TriviaQA()
        data.write_dataset(path)   

download("kilt_nq")
download("kilt_triviaqa")