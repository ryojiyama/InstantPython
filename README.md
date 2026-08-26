# InstantPython
練習用及び簡単なスクリプトを含むリポジトリ

コマンド	説明	実行例
ollama run <モデル名>	モデルを起動し、チャットを開始します。（未ダウンロードの場合は自動でダウンロードされます）	ollama run llama3
ollama list	Mac内にダウンロードされているモデルの一覧を表示します。（lsでも可）	ollama list
ollama pull <モデル名>	モデルのダウンロードのみを行います。（すぐには起動しない場合）	ollama pull mistral
ollama rm <モデル名>	不要になったモデルを削除し、ストレージ容量を解放します。	ollama rm llama3
ollama show <モデル名>	モデルの詳細情報（ライセンス、パラメータ、システムプロンプトなど）を表示します。	ollama show llama3
ollama cp <元> <先>	モデルを複製します。（プロンプトなどをカスタマイズしたい場合に便利です）	ollama cp llama3 my-llama
