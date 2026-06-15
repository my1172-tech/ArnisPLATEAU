# arnis-gui (ArnisPLATEAU GUI 変更分)

このディレクトリは [arnis](https://github.com/louis-e/arnis) に対して加えた
ArnisPLATEAU 向けのカスタマイズ変更ファイルを格納しています。

## 変更ファイル一覧

| ファイル | 変更内容 |
|----------|----------|
| `src/args.rs` | GSI・PLATEAU CLI引数追加 |
| `src/gui.rs` | Java/Bedrock/Luanti切替・GSI+PLATEAU生成処理・PLATEAUカバレッジAPI (`gui_fetch_plateau_coverage`) |
| `src/gui/index.html` | Java Editionタブ追加、PLATEAU/GSIトグルUI |
| `src/gui/js/main.js` | 生成ボタン再有効化・エラー処理修正 |
| `src/gui/js/bbox.js` | PLATEAUエリアオーバーレイ（ズーム連動・遅延ロード） |
| `src/gui/css/bbox.css` | PLATEAUコントロールパネルのスタイル |

## 元リポジトリ

- arnis 本体: https://github.com/louis-e/arnis
- arnis-jp (本リポジトリ): PLATEAU/GSI対応の日本向け拡張
