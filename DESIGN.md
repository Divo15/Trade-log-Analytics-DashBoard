# Trade-log Analytics Dashboard Design

## Direction

Daylight risk desk: a quiet, high-density workspace used by analysts for long review sessions. The visual strategy is restrained—white working surfaces, cool neutral structure, warm coral only for primary actions, and semantic green/red supported by signs and labels.

## Color tokens

All application colors use OKLCH.

- `--bg: oklch(0.975 0.004 250)`
- `--surface: oklch(1 0 0)`
- `--surface-muted: oklch(0.955 0.006 250)`
- `--ink: oklch(0.225 0.018 255)`
- `--muted: oklch(0.48 0.018 255)`
- `--line: oklch(0.885 0.009 250)`
- `--primary: oklch(0.55 0.16 35.8)`
- `--primary-hover: oklch(0.55 0.17 35.8)`
- `--positive: oklch(0.47 0.115 155)`
- `--negative: oklch(0.53 0.17 25)`
- `--focus: oklch(0.60 0.15 245)`

## Typography

Use Inter when available, followed by the system sans-serif stack. Use tabular numerals for metrics, charts, and tables. Headings are compact and functional; labels use sentence case rather than decorative tracking.

## Layout

The desktop view uses a compact top bar, a KPI strip, a dominant equity panel, supporting metrics, monthly performance, and a raw-trade table. Below 900px, regions stack; tables scroll horizontally. The upload state is focused and instructional rather than an empty dashboard shell.

## Components

- Report section navigation highlights the visible section and scrolls to performance, intraday risk, consistency, monthly results, and batches.
- Intraday risk uses a compact metrics column beside the equity chart on desktop, stacking on narrow screens.
- Batch search and outcome filters explicitly show the number of matching loaded batches; they apply only to the latest 100.
- Table labels and supporting metric text use a readable 12px minimum; chart scales include currency labels.
- Dataset selection shows the detected usable date coverage and automatically runs the complete range; users do not re-enter dates already defined by the data.
- Lot size and capital controls stay out of the general run form because those assumptions belong to the uploaded strategy's execution model.

- Buttons and inputs use 8px radii; panels use 12px.
- Primary actions use the coral brand color with white text.
- Success and loss states always include a sign or label in addition to color.
- Focus rings are visible and keyboard navigation follows document order.
- Charts use direct labels and tooltips; motion is limited to state transitions and disabled under reduced-motion preferences.
