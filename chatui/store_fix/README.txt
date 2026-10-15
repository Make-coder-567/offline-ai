【离线AI聊天工具 · 把模型改存到非系统盘】

为什么需要它：
  您的 C 盘（系统盘）剩余空间不足（模型需要约 14GB）。
  现象就是：0.6B/1B 等小模型能装好，3B/4B 等大模型装不上。
  本工具把 9 个模型改存到其它盘，不再占用 C 盘，也无需重新下载任何文件。

—— 使用方法 ——
1. 把 start_models_on_D.bat 复制到 offline_chat 文件夹里
   （就是包含 offline_chat.exe 的那个文件夹）。
2. 双击 start_models_on_D.bat。
   · 窗口会显示目标文件夹和该盘剩余空间；
   · 目标盘需要约 15GB 空闲。默认使用本程序所在的盘（例如 D:\ollama_models）；
   · 若该盘空间也不够：按 Ctrl+C 停掉，用记事本打开这个 bat，
     把这一行  set "MODELS_DRIVE=%~d0"  改成  set "MODELS_DRIVE=E:"
     （换成空间充足的盘符），保存后重新双击运行。
3. 脚本会先结束残留的聊天程序/ollama 进程，再启动 offline_chat.exe。
   首次运行会把 9 个模型全部导入到上面那个文件夹（约 5~20 分钟），
   显示"正在导入内置模型"时请不要关闭窗口。

—— 以后怎么启动 ——
· 建议始终用 start_models_on_D.bat 启动；
· 重启过一次电脑后，直接双击 offline_chat.exe 也可以（模型路径已写入系统设置）。

—— 可选：回收 C 盘空间 ——
确认新目录里 9 个模型都导入成功后，可以删除 C 盘上已经作废的旧模型：
    C:\Users\<你的用户名>\.ollama\models
（删掉它不影响存在新目录里的模型）

—— 仍然失败时 ——
把 offline_chat 文件夹里的 diagnose_result.txt 和 offline_chat_debug.log
发回给发送人即可定位。
