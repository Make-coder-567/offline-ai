#!/bin/bash
# 从 ModelScope(魔搭) 下载 GGUF 并导入 Ollama
# 背景: 本环境无法访问 registry.ollama.ai / huggingface.co, 但 modelscope.cn 可达
# 所有仓库名与文件名均已通过 API 逐一核实存在
set -u
OLLAMA="/c/Users/mrhua/AppData/Local/Programs/Ollama/ollama.exe"
DL="E:/Workbuddy/2026-10-11-17-27-15/offline-ai/chatui/gguf_models"
mkdir -p "$DL"

make_modelfile() {   # $1=输出路径 $2=gguf绝对路径 $3=模板
  {
    echo "FROM $2"
    case "$3" in
      qwen3)
        echo 'TEMPLATE """{{ if .System }}<|im_start|>system'
        echo '{{ .System }}<|im_end|>'
        echo '{{ end }}<|im_start|>user'
        echo '{{ .Prompt }}<|im_end|>'
        echo '<|im_start|>assistant'
        echo '{{ .Response }}<|im_end|>"'
        echo 'PARAMETER stop "<|im_start|>"'
        echo 'PARAMETER stop "<|im_end|>"'
        echo 'PARAMETER temperature 0.6'
        echo 'PARAMETER top_p 0.95'
        echo 'PARAMETER top_k 20'
        ;;
      gemma3)
        echo 'TEMPLATE """<bos><start_of_turn>user'
        echo '{{ .Prompt }}<end_of_turn>'
        echo '<start_of_turn>model'
        echo '{{ .Response }}<end_of_turn>"'
        echo 'PARAMETER stop "<end_of_turn>"'
        echo 'PARAMETER stop "<eos>"'
        echo 'PARAMETER temperature 1.0'
        echo 'PARAMETER top_k 64'
        ;;
      llama3)
        echo 'TEMPLATE """<|start_header_id|>user<|end_header_id|>'
        echo ''
        echo '{{ .Prompt }}<|eot_id|><|start_header_id|>assistant<|end_header_id|>'
        echo ''
        echo '{{ .Response }}<|eot_id|>"'
        echo 'PARAMETER stop "<|eot_id|>"'
        echo 'PARAMETER stop "<|end_of_text|>"'
        echo 'PARAMETER temperature 0.6'
        echo 'PARAMETER top_p 0.9'
        ;;
      *) # chatml (coder / deepseek)
        echo 'TEMPLATE """{{ if .System }}<|im_start|>system'
        echo '{{ .System }}<|im_end|>'
        echo '{{ end }}<|im_start|>user'
        echo '{{ .Prompt }}<|im_end|>'
        echo '<|im_start|>assistant'
        echo '{{ .Response }}<|im_end|>"'
        echo 'PARAMETER stop "<|im_start|>"'
        echo 'PARAMETER stop "<|im_end|>"'
        echo 'PARAMETER temperature 0.6'
        echo 'PARAMETER top_p 0.95'
        ;;
    esac
  } > "$1"
}

# $1=魔搭仓库 $2=gguf文件名 $3=Ollama标签 $4=模板
pull_one() {
  REPO="$1"; FILE="$2"; TAG="$3"; TPL="$4"
  OUT="$DL/$FILE"
  echo "=========================================="
  echo "[MODEL] $TAG   (魔搭 $REPO/$FILE)"
  if [ ! -s "$OUT" ] || [ "$(head -c 4 "$OUT" 2>/dev/null)" != "GGUF" ]; then
    URL="https://modelscope.cn/api/v1/models/$REPO/repo?Revision=master&FilePath=$FILE"
    curl -L --max-time 3600 -o "$OUT" "$URL" 2>/dev/null
    if [ "$(head -c 4 "$OUT" 2>/dev/null)" != "GGUF" ]; then
      echo "[FAIL] 下载无效(GGUF magic 校验失败): $TAG"; rm -f "$OUT"; return 1
    fi
    echo "[DL-OK] $(du -h "$OUT" | cut -f1)"
  else
    echo "[CACHED] $(du -h "$OUT" | cut -f1)"
  fi

  MF="$DL/Modelfile.$(echo "$TAG" | tr ':' '_')"
  make_modelfile "$MF" "$OUT" "$TPL"
  if "$OLLAMA" create "$TAG" -f "$MF" >/dev/null 2>&1 && "$OLLAMA" list 2>/dev/null | grep -q "^${TAG}"; then
    echo "[OK] 导入成功: $TAG"
  else
    echo "[FAIL] ollama create 失败: $TAG"; "$OLLAMA" create "$TAG" -f "$MF" 2>&1 | tail -3; return 1
  fi
}

pull_one "Qwen/Qwen3-0.6B-GGUF"    "Qwen3-0.6B-Q8_0.gguf"        "qwen3:0.6b"        "qwen3"
pull_one "Qwen/Qwen3-1.7B-GGUF"    "Qwen3-1.7B-Q8_0.gguf"        "qwen3:1.7b"        "qwen3"
pull_one "unsloth/gemma-3-1b-it-GGUF" "gemma-3-1b-it-Q4_K_M.gguf" "gemma3:1b"         "gemma3"
pull_one "unsloth/DeepSeek-R1-Distill-Qwen-1.5B-GGUF" "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf" "deepseek-r1:1.5b" "chatml"
pull_one "unsloth/Llama-3.2-1B-Instruct-GGUF" "Llama-3.2-1B-Instruct-Q4_K_M.gguf" "llama3.2:1b" "llama3"
pull_one "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF" "qwen2.5-coder-3b-instruct-q4_k_m.gguf" "qwen2.5-coder:3b" "chatml"
pull_one "unsloth/Llama-3.2-3B-Instruct-GGUF" "Llama-3.2-3B-Instruct-Q4_K_M.gguf" "llama3.2:3b" "llama3"
pull_one "Qwen/Qwen3-4B-GGUF"      "Qwen3-4B-Q4_K_M.gguf"        "qwen3:4b"          "qwen3"
pull_one "unsloth/gemma-3-4b-it-GGUF" "gemma-3-4b-it-Q4_K_M.gguf" "gemma3:4b"         "gemma3"
pull_one "Qwen/Qwen3-8B-GGUF"      "Qwen3-8B-Q4_K_M.gguf"        "qwen3:8b"          "qwen3"

echo "=========================================="
echo "[DONE] 最终模型列表:"
"$OLLAMA" list