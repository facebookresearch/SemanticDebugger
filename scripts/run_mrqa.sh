task="mrqa_squad"
python src/cli_base.py \
        --do_train \
        --output_dir out/${task} \
        --model facebook/bart-large \
        --dataset ${task} \
        --train_file data/${task}/${task}_train.tsv \
        --dev_file data/${task}/${task}_dev.mini.tsv \
        --test_file data/${task}/${task}_dev.tsv \
        --learning_rate 1e-5 \
        --warmup_steps 100 \
        --train_batch_size 16 \
        --predict_batch_size 16 \
        --eval_period 300 \
        --num_train_epochs 10 \
        --max_input_length 888 \
        --max_output_length 50 \
        --num_beams 3 \
        --append_another_bos  > logs/train_${task}.log 2>&1 &

tail -f logs/train_${task}.log