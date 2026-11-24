# Technology Adaptation Observatory

TAOは、組織が技術を採用し、維持し、環境の変化に応じて改善する過程を観測するプロジェクトです。技術選択が利用者、現場、事業にもたらす効果を、工学上の要件と経済性の両面から検討します。最初の観測単位は、未ログインで閲覧できる航空会社の公開ホームページです。

## 最初のマイルストーン

Python 3.9以上で、ローカルの保存済みスナップショットから証拠と確度をJSONとして再生成できます。入力例は架空の航空会社です。ネットワークアクセスも実行時の外部依存もありません。

```sh
python3 -m tao replay tests/fixtures/home.snapshot.json
python3 -m unittest discover -s tests -v
```

パッケージを環境にインストールした場合は `tao replay tests/fixtures/home.snapshot.json` でも実行できます。CLIは標準出力にJSONを出し、入力エラーの場合は標準エラーに理由を出して終了コード2を返します。実サイトの保存データは `captures/` に置き、公開前に別途レビューしてください。

| 成果物 | 内容 |
| --- | --- |
| [Phase 0研究設計](docs/phase-0.md) | 研究仮説、観測方法、終了条件、段階計画 |
| [実現性とライセンス](docs/feasibility.md) | ツール・データの採否、航空会社の候補と保留事項 |
| [観測仕様](docs/methodology.md) | 入出力、証拠、確度、評価基準のバージョン |
| [Phase 1 PR計画](docs/phase-1.md) | GitHub / Codexに引き継ぐ作業範囲、受け入れテスト |
| [観測JSON Schema](schemas/observation-v1.0.0.schema.json) | 出力の機械可読契約 |
| [パイロット候補台帳](pilot/airlines-v0.2.0.json) | 対象ページ、地域、ポリシー参照、収集の審査状態 |
