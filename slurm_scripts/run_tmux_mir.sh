
declare -a seeds=("42" "0212" "1213" "2021" "123" "456" "567" "789")
for seed in "${seeds[@]}"
do
session_name=mir-c512_T100_F3_${seed}
tmux new-session -d -s ${session_name} "srun --job-name ${session_name} --gpus-per-node=1 --partition=devlab --time=180 --cpus-per-task 8 --pty exp_results/dynamic_stream/memory_based/run_mir.sh 100 32 3 512 none 0.5 ${seed}"
echo "Created tmux session: ${session_name}"
done

