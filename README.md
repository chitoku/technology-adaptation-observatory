# Technology Adaptation Observatory

TAOは、組織が技術を採用し、維持し、環境の変化に応じて改善する過程を観測するプロジェクトです。技術選択が利用者、現場、事業にもたらす効果を、工学上の要件と経済性の両面から検討します。最初の観測単位は、未ログインで閲覧できる航空会社の公開ホームページです。

## CLI

Python 3.9以上で、ローカルの保存済みスナップショットから証拠と確度をJSONとして再生成できます。入力例は架空の航空会社です。ネットワークアクセスも実行時の外部依存もありません。

```sh
python3 -m tao replay tests/fixtures/home.snapshot.json
python3 -m unittest discover -s tests -v
```

パッケージを環境にインストールした場合は `tao replay tests/fixtures/home.snapshot.json` でも実行できます。CLIは標準出力にJSONを出し、入力エラーの場合は標準エラーに理由を出して終了コード2を返します。実サイトの保存データは `captures/` に置き、公開前に別途レビューしてください。

### 単発収集と未取得の記録

`capture` は台帳から1件を選び、審査済みの公開ホームだけを単発取得します。候補台帳の現行版では全社が審査または許可待ちです。次の例はAmericanの未取得理由を記録し、その記録を再生します。接続が発生する条件と審査記録の形式は観測仕様にあります。 [単発収集の仕様](docs/methodology.md#単発収集プロファイル-public-http1)

```sh
python3 -m pip install '.[capture]'
mkdir -p captures
tao capture american-en-home --manifest pilot/airlines-v0.2.0.json --region US --out captures/american.json
tao replay captures/american.json
```

### 評価とJSONL出力

公開ホームの最終URLがHTTPSかを、評価基準 `0.1.0` で判定します。書き出しには比較対象の台帳と、対象ごとに選んだ観測JSONを渡します。対象行ごとに判定、証拠参照、出典、欠測理由を、末尾に集計を出力します。下記はすべて合成データです。

```sh
python3 -m tao evaluate tests/fixtures/home.observation.json \
  --surface consumer_homepage --rubric-version 0.1.0
python3 -m tao export --manifest tests/fixtures/home.manifest.json \
  --rubric-version 0.1.0 tests/fixtures/home.observation.json
```

CIはPython 3.9・3.12・3.14で、再生・評価・出力テスト、Draft 2020-12のスキーマ検証、ローカル収集テスト、インストール済みCLIの動作確認を実行します。追加テストを手元で実行するには、開発環境で以下を実行してください。

```sh
python3 -m pip install '.[capture,test]'
python3 -m unittest discover -s tests -p schema_contract.py -v
python3 -m unittest discover -s tests -p capture_flow.py -v
```

| 成果物 | 内容 |
| --- | --- |
| [Phase 0研究設計](docs/phase-0.md) | 研究仮説、観測方法、終了条件、段階計画 |
| [実現性とライセンス](docs/feasibility.md) | ツール・データの採否、航空会社の候補と保留事項 |
| [観測仕様](docs/methodology.md) | 入出力、証拠、確度、評価基準のバージョン |
| [Phase 1 PR計画](docs/phase-1.md) | GitHub / Codexに引き継ぐ作業範囲、受け入れテスト |
| [観測JSON Schema 1.0](schemas/observation-v1.0.0.schema.json) / [1.1](schemas/observation-v1.1.0.schema.json) | 従来入力と単発収集の出力契約 |
| [パイロット候補台帳](pilot/airlines-v0.2.0.json) | 対象ページ、地域、ポリシー参照、収集の審査状態 |
