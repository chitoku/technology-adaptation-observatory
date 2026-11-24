# TAO Scannerの技術構成観測

`tao stack tests/fixtures/stack.html` はローカルのUTF-8 HTMLを読み、技術名の判定とバージョンの表示を別々に返す。単発収集でも同じ判定を実行する。収集結果では `capture_context.technology_stack` に判定ルールと仕様の版、観測レイヤー、HTMLのハッシュ、スクリプトの位置と手掛かりを記録する。元のHTML、スクリプトURL、インラインデータはメモリ内で処理する。キャプチャ1.2と観測出力1.2を使用し、従来の1.0・1.1も再生できる。 [技術判定JSON Schema](../schemas/technology-stack-v0.1.0.schema.json) · [観測JSON Schema 1.2](../schemas/observation-v1.2.0.schema.json)

## 最初のルール

- jQueryとReactは、区別できるスクリプト参照を `referenced` とする。参照に明記された3桁構成の版は `labelled`、異なる表記があれば `multiple_labels` とする。実行されたコードの版は別の証拠を要する。
- Next.js Pages Routerは特徴的なページデータと静的チャンクの参照がそろった場合に `inferred` とする。そこからReactの利用も推定し、版は独立して扱う。React DOMの参照だけの場合もReactの利用は推定に留める。 [Next.jsページデータ](https://nextjs.org/docs/messages/large-page-data) · [静的アセット](https://nextjs.org/docs/app/api-reference/config/next-config-js/assetPrefix)
- 判定材料が足りなければ `unknown`。取得不能や途中打ち切りは、完全HTMLからの手掛かり不足とは異なる `coverage: unavailable` を持つ。App Router単独や一般名のJSバンドルには追加レイヤーが必要になる。

## 観測レイヤーと既存ツール

レコードはページ・取得時点・レイヤーごとに記録する。次のアダプターも、レイヤー名、入力の粒度、ツールとルールの版、証拠の位置を持つ独立レコードを返す形にする。同一ページの複数レイヤーは集約時に関連づけ、取得条件や判定が異なれば両方の根拠を残す。

| レイヤー／資料 | 使い方の候補 |
| --- | --- |
| Static HTML | 今回の実装。Wappalyzer系の判定定義を比較する。HTTP ArchiveフォークのGPL条件は依存追加・定義の取り込み前に確認する。 [HTTP Archiveのフォーク](https://github.com/HTTPArchive/wappalyzer) |
| JavaScript Assets | 許可された配信済み成果物のライブラリ識別・版確認にRetire.js系の既存ルールを検討する。識別データの再利用条件を確認する。 [Retire.js](https://github.com/RetireJS/retire.js) |
| Runtime JavaScript・Rendered DOM・Network Activity | 許可済みの公開ページを通常表示し、Playwrightを実行側に、CDPを必要なイベントの観測に使う。各レイヤーで取得条件を個別に残す。 [Playwright Network](https://playwright.dev/docs/network) · [CDP](https://chromedevtools.github.io/devtools-protocol/) |
| HTTP Archive | 発行月・対象URL・端末・判定器を付けて、別時点・別条件の既存データとして参照候補にする。 [技術データ仕様](https://httparchive.org/docs/reference/structs/technology) |

台帳の `surface` は `consumer_homepage`、`booking_entry`、`public_membership` を受け付ける。公開入口で観測した技術の適用範囲はそのページとし、ログイン後の処理や別の予約サービスの構成は別途裏付ける。公開ホームのHTTPS評価は従来どおりホームに適用する。
