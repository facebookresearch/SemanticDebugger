from semanticdebugger.debug_algs.cl_utils import get_top_interfered_examples,  local_adaptation, KeyValueMemoryModule
from transformers.optimization import AdamW, get_linear_schedule_with_warmup
from semanticdebugger.debug_algs.cl_simple_alg import ContinualFinetuning
from tqdm import tqdm
import random
import numpy as np
import torch
import transformers
from semanticdebugger.debug_algs.index_based.index_manager import RandomMemoryManger
from semanticdebugger.task_manager.eval_metrics import evaluate_func
import copy
import pickle
import os

from semanticdebugger.models.utils import (convert_model_to_single_gpu,
                                           freeze_embeds, trim_batch)
                                           
from argparse import Namespace
import more_itertools

class MemoryBasedCL(ContinualFinetuning):
    def __init__(self, logger):
        super().__init__(logger=logger)
        self.name = "tbd"  # can be er/mbpa/mbpa++

    def _check_debugger_args(self):
        super()._check_debugger_args()
        required_atts = [
            "replay_size",
            "replay_candidate_size",
            "replay_frequency",
            "memory_key_encoder",  # 'bert-base-uncased' by default
            "memory_store_rate",  # 0, 0.1, 1 etc.
            "memory_path",  # to save/load the memory module from disk
            "init_memory_cache_path",
            "num_adapt_epochs",
            "inference_query_size",
            "local_adapt_lr",
            "use_replay_mix",
        ]
        assert all([hasattr(self.debugger_args, att) for att in required_atts])

    def debugger_setup(self, debugger_args):

        super().debugger_setup(debugger_args)

        # Initializing the Key-Value memory module for MBPA++
        if self.name in ["er", "mir"]: 
            self.memroy_module = RandomMemoryManger(self.logger) 
            self.logger.info("Prepare the sampled upstream data as the initial memory for the ER and MIR;")
            self.memroy_module.set_up_initial_memory(formatted_examples=self.sampled_upstream_examples)
            self.logger.info(f"Initial memory size: {self.memroy_module.get_memory_size()}")
        elif self.name in ["mbpa", "mbpa++"]:
            # TODO: prepare the Memory module for it
            pass 
        return

    

    # The new evaluation pipeline.

    def online_debug(self):
        self.logger.info("Start Online Debugging with Dynamic Error Mode")
        self.logger.info(f"Number of Batches of Data: {self.num_data_batches}")
        self.logger.info(f"Data Batch Size: {self.data_batch_size};")
        self.timecode = 0

        if self.debugger_args.save_all_ckpts:
            # save the initial model as the 0-th model.
            self._save_base_model()

        self.overall_errors = []
        self.seen_stream_data = []
        last_steps = 0
 
        for data_eval_loader in tqdm(self.data_eval_loaders, desc="Online Debugging (Dynamic)"):

            result_dict = {"timecode": self.timecode}   # start with 0

            self._replay_based_eval(result_dict)
            formatted_bug_examples = self._get_dynamic_errors(
                data_eval_loader, result_dict, return_raw_bug_examples=True)
            
            examples_to_train = formatted_bug_examples[:]
            
            if self.timecode % self.debugger_args.replay_frequency == 0 \
                    and self.debugger_args.replay_frequency > 0 and self.debugger_args.replay_size > 0 \
                    and self.timecode > 0:
                # sparse experience replay
                self.logger.info("Triggering Sampling from Memory and starting to replay.")
                self.logger.info(f"Current memory size: {self.memroy_module.get_memory_size()}.")
                if self.name == "mir":
                    assert self.debugger_args.replay_candidate_size >= self.debugger_args.replay_size
                    retrieved_examples_candidates = self.memroy_module.retrieve_from_memory(
                        sample_size=self.debugger_args.replay_candidate_size)
                    result_dict["mir_buffer_ids"] = [_id for (_input, _truth, _id) in retrieved_examples_candidates]
                    retrieved_examples = get_top_interfered_examples(self,
                        K=self.debugger_args.replay_size, candidate_examples=retrieved_examples_candidates, query_data_loader=bug_train_loader)
                    self.logger.info(f"retrieved_examples (mir)={retrieved_examples}") 
                else:
                    retrieved_examples = self.memroy_module.retrieve_from_memory(
                        sample_size=self.debugger_args.replay_size)

                self.base_model.train()

                result_dict["retrieved_ids"] = [_id for (_input, _truth, _id) in retrieved_examples]
                
                if self.debugger_args.use_replay_mix:
                    examples_to_train += retrieved_examples
                    self.logger.info(f"Mixed the retrieved examples (len={len(retrieved_examples)}) to the current batch for training.")
                else:
                    self.logger.info(f"Replay-Training Start! Using the retrieved examples (len={len(retrieved_examples)})  ")
                    replay_data_loader, _ = self.get_dataloader(
                        self.data_args, retrieved_examples, mode="train")
                    self.fix_bugs(replay_data_loader, quiet=False)  # sparse replay
                    self.logger.info("Replay-Training done.")
            
            last_steps = self.model_update_steps

            ############### CORE ###############
            # Fix the bugs by mini-batch based "training"
            self.logger.info(f"Start bug-fixing (len(examples_to_train)={len(examples_to_train)}) .... Timecode: {self.timecode}")
            bug_train_loader, _ = self.get_dataloader(
                self.data_args, examples_to_train, mode="train")
            self.fix_bugs(bug_train_loader)   # for debugging
            self.logger.info("Start bug-fixing .... Done!")
            ############### CORE ###############
            self._log_episode_result(result_dict, data_eval_loader)
            self.timecode += 1

            if self.debugger_args.save_all_ckpts:
                self._save_base_model()

            # Store to memory
            _max = 1000000
            flag_store_examples = bool(random.randrange(0, _max)/_max >=
                                       1 - self.debugger_args.memory_store_rate)
            if flag_store_examples:
                self.logger.info(f"Saving the current error examples (len={len(formatted_bug_examples)}) to the memory.")
                self.logger.info(f"Current memory size: {self.memroy_module.get_memory_size()}.")
                self.memroy_module.store_examples(formatted_bug_examples)
                self.logger.info("Finished.")
            self.logger.info("-"*50)
        #### Final evaluation ####
        self.final_evaluation()

        # Save to path
        self.memroy_module.save_memory_to_path(self.debugger_args.memory_path)


    def evaluate(self, eval_dataloader=None, verbose=False):
        """Evaluates the performance"""

        if self.name not in ["mbpa", "mbpa++"]:
            # ER (no local adpatation).
            # This is for the equvilent version of the replay as the baseline (MbPA++ w/o local adaptation when inference or just simple replay.)
            return super().evaluate(eval_dataloader, verbose)


        if not eval_dataloader:
            eval_dataloader = self.bug_eval_loaders[self.timecode]

        # TODO: reset the bsz for the local adaptation.

        # prepare adapt_dataloaders
        adapt_dataloaders = self.get_adapt_dataloaders(eval_dataloader, verbose=True)

        predictions = self.base_model_infer_with_adaptation(
            eval_dataloader, adapt_dataloaders, verbose)
        assert len(predictions) == len(eval_dataloader)
        predictions = [p.strip() for p in predictions]
        results, return_all = evaluate_func(
            predictions, eval_dataloader.data, self.metric, return_all=True)

        return predictions, results, return_all

    

    ### The Adapatation Related Functions ###

    def get_adapt_dataloaders(self, eval_dataloader=None, verbose=False):
        """Get the adapt_dataloader."""
        adapt_dataloaders = []
        num_batches = len(eval_dataloader.dataloader)
        example_batches = np.array_split(eval_dataloader.data, num_batches)

        # Only allow retrieving from the past memory. (due to offline evaluation)
        past_memory_keys = []
        for key, values in self.memroy_module.memory.items():
            if values[3]-1 <= self.timecode:
                past_memory_keys.append(key)
        if not past_memory_keys:
            adapt_dataloaders = [None for _ in range(len(example_batches))]
            return adapt_dataloaders

        past_memory_keys = np.frombuffer(np.asarray(
            past_memory_keys), dtype=np.float32).reshape(len(past_memory_keys), -1)

        for example_batch in tqdm(example_batches, desc="Retrieving Data from Memory", disable=not verbose):
            # self.logger.info("Memory Retrieving ...")
            # local adaptation for self.base_model of retrieved examples from memory.
            # self.logger.info("Encoding the examples to evaluate...")
            keys = self.memroy_module.encode_examples(example_batch)
            # self.logger.info("Reading memory to get the KNN examples for local adaptation...")
            retrieved_examples = self.memroy_module.query_examples(
                keys, past_memory_keys, k=self.debugger_args.inference_query_size)
            replay_data_loader, _ = self.get_dataloader(
                self.data_args, retrieved_examples, mode="train")
            adapt_dataloaders.append(replay_data_loader)
            # self.logger.info("Memory Retrieving Done ...")

        return adapt_dataloaders

    def base_model_infer_with_adaptation(self, eval_dataloader, adapt_dataloaders, verbose=False):
        self.base_model.eval()
        model = self.base_model if self.n_gpu == 1 else self.base_model.module
        predictions = self.inference_with_adaptation(model, eval_dataloader, adapt_dataloaders, save_predictions=False,
                                                     verbose=verbose, logger=self.logger, return_all=False, predictions_only=True, args=Namespace(quiet=True))
        return predictions

    def inference_with_adaptation(self, model, dev_data, adapt_dataloaders, save_predictions=False, verbose=False, args=None, logger=None, return_all=False, predictions_only=False):
        # model.eval()
        predictions = []
        bos_token_id = dev_data.tokenizer.bos_token_id
        loss = []   # if needed
        if args:
            quiet = args.quiet
        else:
            quiet = False
        if not quiet:
            logger.info("Starting inference ...")
        current_index = 0
        for batch in tqdm(dev_data.dataloader, desc="Inference", disable=not verbose):
            ### Local Adaptation: Start ###
            _model = copy.deepcopy(model)
            adapt_dataloader = adapt_dataloaders[current_index]
            if adapt_dataloader:
                # TODO: debug. deactivate this step? then it should be the same as ER.
                _model = local_adaptation(self, _model, adapt_dataloader)
                pass
            ### Local Adaptation: End ###

            _model.eval()

            ### Inference: Start ###
            if torch.cuda.is_available():
                batch = [b.to(torch.device("cuda")) for b in batch]
            pad_token_id = dev_data.tokenizer.pad_token_id
            batch[0], batch[1] = trim_batch(batch[0], pad_token_id, batch[1])
            outputs = _model.generate(input_ids=batch[0],
                                      attention_mask=batch[1],
                                      num_beams=dev_data.args.num_beams,
                                      max_length=dev_data.args.max_output_length,
                                      decoder_start_token_id=_model.config.bos_token_id,
                                      early_stopping=dev_data.gen_early_stop,)
            for input_, output in zip(batch[0], outputs):
                pred = dev_data.decode(output)
                predictions.append(pred)

            ### Inference: End ###
            current_index += 1
            del _model

        if not quiet:
            logger.info("Starting inference ... Done")

        if predictions_only:
            return predictions
        if save_predictions:
            dev_data.save_predictions(predictions, )
        # logger.info("Starting evaluation metric ...")
        result = dev_data.evaluate(predictions, verbose=verbose)
        # logger.info("Starting evaluation metric ... Done!")
        if return_all:
            return predictions, result, loss
        return result

