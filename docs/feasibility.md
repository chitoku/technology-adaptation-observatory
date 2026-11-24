# Phase 0 実現性とライセンス

確認日 2026-11-23。採否は今回確認できた公開資料に基づく。ツールのコード、検出定義、APIの利用条件、取得データ、第三者の掲載素材について、それぞれの再利用条件を記録する。

| 候補 | 実現性・制約 | Phase 0の判断 |
| --- | --- | --- |
| Python標準ライブラリ | 保存済みJSONの読み込み、ハッシュ、HTML宣言の抽出を依存なしで実装可能。HTMLソースの軽量な解析なので、ブラウザのDOM修復やJS実行後の表示は対象外。 | 採用。自作の合成データで検証。 |
| Wappalyzer商用API | 技術シグナルの照会。APIクレジット・レート制約があり、FAQではデータの共有・再販売を禁止している。個別契約が必要な用途の取り扱いは未確認。 | 取り込み・公開の依存から外す。 [API仕様](https://www.wappalyzer.com/docs/api/v2/basics/) · [利用条件](https://www.wappalyzer.com/terms/) · [API FAQ](https://www.wappalyzer.com/faq/api/) |
| HTTP ArchiveのWappalyzerフォーク | GPL-3.0の公開リポジトリ。HTTP Archiveの検出定義を確認できる。技術名・照合結果は推定であり、ルールの変更により差分が生じる。 | 手法比較の候補。コード・定義の直接取り込みは見送り。将来使う場合は固定リビジョン、対象ファイルのライセンス、配布条件を確認。 [検出仕様](https://httparchive.org/docs/reference/structs/technology) · [LICENSE](https://github.com/HTTPArchive/wappalyzer/blob/main/LICENSE) |
| HTTP Archive / Web Almanac | BigQueryで過去のラボ計測を参照できる。採録URL・月・端末は提供側の標本設計に依存。クエリ課金・処理量と、元の第三者素材を含む行の再配布条件を確認する必要がある。 | 背景比較の有力候補。クエリの試行とパイロット各URLの収録確認は未実施。原データ再配布は保留。利用時は対象月・テーブル・SQL・端末種別を固定し、ドライランと処理量上限を使う。 [BigQueryガイド](https://httparchive.org/docs/guides/getting-started) · [Web Almanac方法論](https://almanac.httparchive.org/en/2025/methodology) |
| Chrome UX Report（CrUX） | GoogleのデータセットはCC BY 4.0。適格性・人気の閾値があり、すべてのURLに値があるわけではない。主に集計期間、URL／オリジン、端末区分を伴うフィールド計測。 | 後続の性能比較候補。パイロットのカバレッジは未照会。帰属表示、期間、粒度、条件を保存し、オリジン値をURL値として扱わない。 [方法論・ライセンス](https://developer.chrome.com/docs/crux/methodology) · [CrUX API](https://developer.chrome.com/docs/crux/api) |
| Lighthouse / PageSpeed Insights | LighthouseコードはApache-2.0。ラボ計測はバージョン、実行条件、負荷で変動する。PageSpeed Insightsはラボとフィールドの両方を扱うサービスで、サービス利用と出力データの条件は別に確認する。 | 性能の後続候補。Phase 1の収集はHTML文書だけ。比較に使う段階で、設定・ツール版を固定し、承認された条件で複数回計測する。 [Lighthouse](https://github.com/GoogleChrome/lighthouse) · [CrUX公式ツール解説](https://developer.chrome.com/docs/crux/methodology/tools) |
| Common Crawl | 過去のキャプチャ候補。時点とURLの欠測、第三者素材の条件、サービス上の用途制限がある。 | 当面保留。WARCや第三者HTMLは配布に組み込まない。 [利用条件](https://www.commoncrawl.org/terms-of-use) |
| 事業者・ベンダーの公式公開資料 | 公開面の変更告知、事例、期間・役割が明記された発表は後続分析の候補。自己報告・宣伝や発表の相互転載に注意する。 | 出典リンク、独自の要約、公開時点、適用範囲を記録。素材の収集・引用・再配布は資料ごとに判断。 |

## 航空会社候補の確認結果

- **ANA**。日本向け公開ホームが閲覧可能。公式のrobots.txtとサイト利用規約を確認した。規約の転用に関する記述を踏まえ、HTML保存と成果の掲載範囲は事前審査に回す。 [ホーム](https://www.ana.co.jp/ja/jp/) · [robots.txt](https://www.ana.co.jp/robots.txt) · [利用規約](https://www.ana.co.jp/ja/jp/guide/terms/website/)
- **JAL**。旧経路から同じホストの `/ja-jp/` への転送を資料確認時に観測した。公式のrobots.txtと利用規約を確認した。転送前後をプログラムで辿る場合は両経路を先に審査する。 [旧経路](https://www.jal.co.jp/jp/ja/) · [ホーム](https://www.jal.co.jp/ja-jp/) · [robots.txt](https://www.jal.co.jp/robots.txt) · [利用規約](https://www.jal.co.jp/jp/ja/footer/policy/)
- **Delta**。米国向け公開ホームの所在を確認した。収集可否と比較条件の事前審査は未実施。 [ホーム](https://www.delta.com/us/en)
- **American Airlines**。Deltaと同じ用途の米国向け公開ホームを追加候補とした。公式規約はコンテンツのスクレイピングや抽出に明示の書面契約を求めている。台帳は `permission_required` とし、書面で許可を確認できるまでTAOでの収集を保留する。 [ホーム](https://www.aa.com/homePage.do) · [利用規約](https://www.aa.com/web/i18n/customer-service/support/legal-information.html)

資料確認は収集の承認と分けて記録する。対象URL・運用主体ごとに、利用条件、robotsの現在値と適用エージェント、転送先、保存する項目、再配布範囲、審査日を記録する。規約上の扱いが不明な場合は保留し、許可済み公開データや合成入力で開発を進める。robotsは収集可否の判定に使い、権利上の判断は別途行う。 [Robots Exclusion Protocol](https://www.rfc-editor.org/rfc/rfc9309)

## 保存と公開

合成入力はテスト用としてコミットする。実サイトの元レスポンスは承認された場合だけアクセス制限下に置き、初期案では照合期間30日、例外は根拠と期限を記録する。Cookie、認証ヘッダー、クエリ、フォーム入力、個人情報、HTML・JSの全文は公開データに含めない。導出した事実、観測日、対象ページ、検出根拠の位置、版、欠測理由の掲載可否を点検して公開用行を生成する。ハッシュと元資料の保管・開示可否を別に記録し、原資料を開示できない記録は合成データによる手法再現と実サイトの追試の範囲を明示する。TAOのコードとTAO作成データのライセンスは公開決定時に確定し、外部データの出典とライセンス表示は行単位で引き継ぐ。
