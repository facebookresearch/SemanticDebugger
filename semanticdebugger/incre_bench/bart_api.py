import sys
import os
from semanticdebugger.models.mybart import MyBart
from semanticdebugger.models.utils import freeze_embeds, trim_batch, convert_model_to_single_gpu
import json
import torch
from tqdm import tqdm
from transformers import BartTokenizer, BartConfig
from semanticdebugger.task_manager.dataloader import GeneralDataset
from semanticdebugger.models.run_bart import inference
from argparse import Namespace 

def inference_api(config_file, test_file, logger): 
    
    with open(config_file) as f: 
        config_args = eval(f.read())  # an Namespace object in python language  
    args = config_args
    # load config from json  
    
    test_data = GeneralDataset(logger, args, test_file, data_type="dev", is_training=False, task_name=args.dataset)
    tokenizer = BartTokenizer.from_pretrained("bart-large")
    test_data.load_dataset(tokenizer)
    test_data.load_dataloader()

    checkpoint = os.path.join(args.predict_checkpoint)

    logger.info("Loading checkpoint from {} ....".format(checkpoint))
    model = MyBart.from_pretrained(args.model,
                                state_dict=convert_model_to_single_gpu(torch.load(checkpoint)))
    logger.info("Loading checkpoint from {} .... Done!".format(checkpoint))
    if torch.cuda.is_available():
        model.to(torch.device("cuda"))
    model.eval()

    predictions, result, loss = inference(model, test_data, save_predictions=False, verbose=True, args=args, logger=logger, return_all=True)
    return predictions
    # logger.info("%s on %s data: %.s" % (test_data.metric, test_data.data_type, str(result)))


