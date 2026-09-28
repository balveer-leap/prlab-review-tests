import { useMemo } from "react";
import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  CollapsibleSection,
  Divider,
  Grid,
  H2,
  H3,
  Link,
  Pill,
  Row,
  Select,
  Spacer,
  Stack,
  Stat,
  Table,
  Text,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

/*
 * Template for `python3 -m prlab_eval canvas`. The generator reads reports/,
 * replaces the block between the PRLAB_DATA markers, and writes the result to
 * this workspace's Cursor canvases folder. Edit the layout here, not the copy.
 */

/* ------------------------------------------------------------------ data */

type Claim = { id: string; ok: boolean; why: string; q: string };

type PostedComment = { text: string; label: string; why: string };

type CaseRun = {
  pr: string;
  ok: boolean;
  iso: boolean;
  p: number;
  r: number;
  f1: number;
  nc: number;
  rel: number;
  def: number;
  bots: string[];
  claims: Claim[];
  cm: PostedComment[];
};

type CapScore = { p: number; r: number; f1: number };

type ToolRun = {
  tool: string;
  owner: string;
  judge: string;
  basis: string;
  stamp: string;
  report: string;
  rescored: boolean;
  passed: number;
  failed: number;
  p: number;
  r: number;
  f1: number;
  tp: number;
  fn: number;
  fp: number;
  caps: Record<string, CapScore>;
  rows: Record<string, CaseRun>;
};

type CaseMeta = {
  id: string;
  intent: string;
  tests: string;
  cap: string;
  capid: string;
  asks: string;
  claims: { id: string; must: string }[];
};

type Payload = { generatedAt: string; tools: ToolRun[]; cases: CaseMeta[] };

const DATA: Payload = /* PRLAB_DATA */ { generatedAt: "", tools: [], cases: [] } /* END_PRLAB_DATA */;

/**
 * Transparent keyed wrapper. The SDK prop types do not declare `key`, and
 * `display: contents` keeps the wrapper out of flex/grid/table layout.
 */
const PASSTHROUGH = { display: "contents" } as const;

/* --------------------------------------------------------------- helpers */

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function shortCase(id: string): string {
  return id.replace(/^test-/, "");
}

function stampLabel(stamp: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(stamp);
  if (!m) return stamp;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]} UTC`;
}

function maxBy(values: number[]): number {
  return values.reduce((a, b) => (b > a ? b : a), Number.NEGATIVE_INFINITY);
}

/** One standings row: a tool's whole run, or just its result on one case. */
type Standing = {
  run: ToolRun;
  passed: number;
  total: number;
  p: number;
  r: number;
  f1: number;
  tp: number;
  fn: number;
  fp: number;
};

function overallStanding(run: ToolRun): Standing {
  return {
    run,
    passed: run.passed,
    total: run.passed + run.failed,
    p: run.p,
    r: run.r,
    f1: run.f1,
    tp: run.tp,
    fn: run.fn,
    fp: run.fp,
  };
}

function caseStanding(run: ToolRun, caseId: string): Standing | undefined {
  const row = run.rows[caseId];
  if (!row) return undefined;
  const tp = row.claims.filter((c) => c.ok).length;
  return {
    run,
    passed: row.ok ? 1 : 0,
    total: 1,
    p: row.p,
    r: row.r,
    f1: row.f1,
    tp,
    fn: row.claims.length - tp,
    fp: row.nc - row.rel,
  };
}

/* ------------------------------------------------------------ components */

function Verdict({ run }: { run: CaseRun | undefined }) {
  const t = useHostTheme();
  if (!run) {
    return (
      <Text size="small" tone="quaternary">
        not run
      </Text>
    );
  }
  const color = run.ok ? t.category.green : t.category.red;
  return (
    <Row gap={6} align="center">
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: 9999,
          background: color,
          flexShrink: 0,
        }}
      />
      <Text size="small" weight="medium" style={{ color }}>
        {run.ok ? "caught" : "missed"}
      </Text>
      <Text size="small" tone="quaternary">
        {pct(run.p)}p
      </Text>
    </Row>
  );
}

function LabelChip({ label }: { label: string }) {
  const t = useHostTheme();
  const color =
    label === "trap"
      ? t.category.green
      : label === "defect"
        ? t.category.blue
        : t.category.gray;
  return (
    <span
      style={{
        fontSize: 11,
        lineHeight: "16px",
        padding: "0 6px",
        borderRadius: 4,
        border: `1px solid ${color}`,
        color,
        fontWeight: 600,
      }}
    >
      {label}
    </span>
  );
}

function CommentBlock({ comment, index }: { comment: PostedComment; index: number }) {
  const t = useHostTheme();
  return (
    <Stack gap={4}>
      <Row gap={8} align="center">
        <Text size="small" tone="quaternary">
          Comment {index}
        </Text>
        <LabelChip label={comment.label} />
        {comment.why ? (
          <Text size="small" tone="tertiary">
            {comment.why}
          </Text>
        ) : null}
      </Row>
      <div
        style={{
          background: t.fill.quaternary,
          border: `1px solid ${t.stroke.tertiary}`,
          borderRadius: 6,
          padding: "8px 10px",
          fontSize: 12,
          lineHeight: "17px",
          color: t.text.secondary,
          whiteSpace: "pre-wrap",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        }}
      >
        {comment.text}
      </div>
    </Stack>
  );
}

function ToolDetailCard({
  run,
  toolName,
  meta,
}: {
  run: CaseRun;
  toolName: string;
  meta: CaseMeta;
}) {
  const t = useHostTheme();
  const color = run.ok ? t.category.green : t.category.red;
  return (
    <Card>
      <CardHeader
        trailing={
          <Text size="small" weight="medium" style={{ color }}>
            {run.ok ? "CAUGHT" : "MISSED"}
          </Text>
        }
      >
        {toolName}
      </CardHeader>
      <CardBody>
        <Stack gap={12}>
          <Row gap={16} align="center" wrap>
            <Stat value={pct(run.p)} label="precision" />
            <Stat value={pct(run.r)} label="recall" />
            <Stat value={`${run.rel}/${run.nc}`} label="relevant comments" />
            <Spacer />
            <Row gap={8} align="center">
              <Link href={run.pr}>Pull request</Link>
              <Text size="small" tone="quaternary">
                ·
              </Text>
              <Link href={`${run.pr}/files`}>Inline comments</Link>
            </Row>
          </Row>

          {!run.iso ? (
            <Callout tone="warning" title="Isolation failed">
              <Text size="small">
                Unexpected bots on this PR: {run.bots.join(", ")}
              </Text>
            </Callout>
          ) : null}

          <Divider />

          <Stack gap={10}>
            {run.claims.map((claim) => {
              const must = meta.claims.find((c) => c.id === claim.id)?.must;
              const claimColor = claim.ok ? t.category.green : t.category.red;
              return (
                <div key={claim.id} style={PASSTHROUGH}>
                  <Stack gap={4}>
                    <Row gap={8} align="center">
                      <Text
                        size="small"
                        weight="semibold"
                        style={{ color: claimColor }}
                      >
                        {claim.ok ? "PASS" : "FAIL"}
                      </Text>
                      <Text size="small" weight="medium">
                        {claim.id}
                      </Text>
                    </Row>
                    {must ? (
                      <Text size="small" tone="tertiary">
                        Must assert: {must}
                      </Text>
                    ) : null}
                    <Text size="small" tone="secondary">
                      Judge: {claim.why}
                    </Text>
                    {claim.q ? (
                      <Text size="small" tone="tertiary" italic>
                        Evidence quoted: “{claim.q}”
                      </Text>
                    ) : null}
                  </Stack>
                </div>
              );
            })}
          </Stack>

          {run.cm.length > 0 ? (
            <CollapsibleSection
              title="Comments posted on the PR"
              count={run.cm.length}
              defaultOpen
            >
              <Stack gap={12}>
                {run.cm.map((comment, i) => (
                  <div key={i} style={PASSTHROUGH}>
                    <CommentBlock comment={comment} index={i + 1} />
                  </div>
                ))}
              </Stack>
            </CollapsibleSection>
          ) : null}
        </Stack>
      </CardBody>
    </Card>
  );
}

function StandingsTable({ rows }: { rows: Standing[] }) {
  const t = useHostTheme();
  const bestF1 = maxBy(rows.map((x) => x.f1));
  const bestRecall = maxBy(rows.map((x) => x.r));
  const bestPrecision = maxBy(rows.map((x) => x.p));
  const lead = (on: boolean) =>
    on ? { color: t.accent.primary } : undefined;
  return (
    <Table
      headers={[
        "Tool",
        "Cases passed",
        "Precision",
        "Recall",
        "F1",
        "TP / FN / FP",
        "Run",
      ]}
      columnAlign={["left", "right", "right", "right", "right", "right", "left"]}
      rowTone={rows.map((x) =>
        rows.length > 1 && x.f1 === bestF1 ? ("success" as const) : undefined,
      )}
      rows={rows.map((x) => [
        <div key="n" style={PASSTHROUGH}>
          <Stack gap={2}>
            <Text weight="semibold">{x.run.tool}</Text>
            <Text size="small" tone="quaternary">
              {x.run.owner}
            </Text>
          </Stack>
        </div>,
        <div key="c" style={PASSTHROUGH}>
          <Text weight="medium">
            {x.passed}/{x.total}
          </Text>
        </div>,
        <div key="p" style={PASSTHROUGH}>
          <Text
            weight={x.p === bestPrecision ? "semibold" : "normal"}
            style={lead(x.p === bestPrecision)}
          >
            {pct(x.p)}
          </Text>
        </div>,
        <div key="r" style={PASSTHROUGH}>
          <Text
            weight={x.r === bestRecall ? "semibold" : "normal"}
            style={lead(x.r === bestRecall)}
          >
            {pct(x.r)}
          </Text>
        </div>,
        <div key="f" style={PASSTHROUGH}>
          <Text
            weight={x.f1 === bestF1 ? "semibold" : "normal"}
            style={lead(x.f1 === bestF1)}
          >
            {pct(x.f1)}
          </Text>
        </div>,
        <div key="t" style={PASSTHROUGH}>
          <Text size="small" tone="tertiary">
            {x.tp} / {x.fn} / {x.fp}
          </Text>
        </div>,
        <div key="s" style={PASSTHROUGH}>
          <Stack gap={2}>
            <Text size="small" tone="tertiary">
              {stampLabel(x.run.stamp)} · judge {x.run.judge}
              {x.run.rescored ? " · rescored" : ""}
            </Text>
            <Text size="small" tone="quaternary">
              {x.run.report}
            </Text>
          </Stack>
        </div>,
      ])}
    />
  );
}

/* ------------------------------------------------------------------ page */

export default function ReviewToolComparison() {
  const t = useHostTheme();
  const allToolNames = DATA.tools.map((x) => x.tool);

  const [selected, setSelected] = useCanvasState<string[]>(
    "selectedTools",
    allToolNames,
  );
  const [caseId, setCaseId] = useCanvasState<string>("selectedCase", "all");

  const tools = useMemo(
    () => DATA.tools.filter((x) => selected.includes(x.tool)),
    [selected],
  );

  function toggleTool(name: string) {
    setSelected((prev) => {
      if (prev.includes(name)) {
        const next = prev.filter((x) => x !== name);
        return next.length === 0 ? prev : next;
      }
      return allToolNames.filter((x) => prev.includes(x) || x === name);
    });
  }

  const capIds = useMemo(() => {
    const seen: string[] = [];
    for (const c of DATA.cases) if (!seen.includes(c.capid)) seen.push(c.capid);
    return seen;
  }, []);

  const activeCase =
    caseId === "all" ? undefined : DATA.cases.find((c) => c.id === caseId);

  const standings: Standing[] = activeCase
    ? tools
        .map((x) => caseStanding(x, activeCase.id))
        .filter((x): x is Standing => x !== undefined)
    : tools.map(overallStanding);

  const unrated = tools.filter((x) => x.basis !== "rated").map((x) => x.tool);

  const matrixRows = DATA.cases.map((meta) => {
    const runs = tools.map((tr) => tr.rows[meta.id]);
    const caught = runs.filter((r) => r && r.ok).length;
    const bestCellP = maxBy(
      runs.filter((r) => r && r.ok).map((r) => (r ? r.p : 0)),
    );
    const cells = tools.map((tr, i) => {
      const run = runs[i];
      const isBest =
        !!run && run.ok && caught > 0 && run.p === bestCellP && tools.length > 1;
      return (
        <div key={tr.tool} style={PASSTHROUGH}>
          <Row gap={6} align="center">
            <Verdict run={run} />
            {isBest ? (
              <span
                style={{
                  fontSize: 10,
                  lineHeight: "14px",
                  padding: "0 5px",
                  borderRadius: 9999,
                  color: t.text.onAccent,
                  background: t.accent.primary,
                }}
              >
                best
              </span>
            ) : null}
          </Row>
        </div>
      );
    });
    return {
      meta,
      caught,
      cells,
      tone:
        caught === 0
          ? ("danger" as const)
          : caught === tools.length
            ? ("success" as const)
            : ("warning" as const),
    };
  });

  const hardest = [...matrixRows]
    .filter((r) => r.caught < tools.length)
    .sort((a, b) => a.caught - b.caught);

  return (
    <Stack gap={24} style={{ padding: 24, maxWidth: 1320 }}>
      {/* header */}
      <Stack gap={6}>
        <Text
          size="small"
          tone="quaternary"
          style={{ letterSpacing: 0.4, textTransform: "uppercase" }}
        >
          prlab-review-tests
        </Text>
        <Text
          style={{
            fontSize: 24,
            lineHeight: "30px",
            fontWeight: 590,
            color: t.text.primary,
          }}
        >
          Which PR review tool catches the cricket traps?
        </Text>
        <Text tone="secondary">
          {activeCase
            ? `Showing one case: ${shortCase(activeCase.id)}. Pick "All ${DATA.cases.length} cases" to return to the full comparison.`
            : `${DATA.tools.length} review products scored against the same ${DATA.cases.length} planted-bug cases. Each tool ran in its own GitHub org so only its own bot could comment.`}
        </Text>
        <Text size="small" tone="quaternary">
          Source: latest run per tool under reports/&lt;tool&gt;/, generated{" "}
          {stampLabel(DATA.generatedAt)} by python3 -m prlab_eval canvas.
        </Text>
      </Stack>

      {/* selectors */}
      <Grid columns="minmax(0, 1fr) minmax(0, 1.5fr)" gap={20} align="start">
        <Stack gap={8}>
          <Text size="small" weight="semibold" tone="secondary">
            Case
          </Text>
          <Select
            value={caseId}
            onChange={setCaseId}
            options={[
              { value: "all", label: `All ${DATA.cases.length} cases` },
              ...DATA.cases.map((c) => ({
                value: c.id,
                label: shortCase(c.id),
              })),
            ]}
          />
        </Stack>

        <Stack gap={8}>
          <Text size="small" weight="semibold" tone="secondary">
            Reviewer
          </Text>
          <Row gap={8} wrap>
            <Pill
              active={selected.length === allToolNames.length}
              onClick={() => setSelected(allToolNames)}
            >
              All
            </Pill>
            {DATA.tools.map((tr) => (
              <div key={tr.tool} style={PASSTHROUGH}>
                <Pill
                  active={selected.includes(tr.tool)}
                  onClick={() => toggleTool(tr.tool)}
                >
                  {tr.tool}
                </Pill>
              </div>
            ))}
          </Row>
        </Stack>
      </Grid>

      {/* standings */}
      <Stack gap={8}>
        <H2>
          {activeCase
            ? `Standings on ${shortCase(activeCase.id)}`
            : "Overall standings"}
        </H2>
        <Text size="small" tone="tertiary">
          Recall = planted traps the tool asserted ÷ planted traps. Precision =
          relevant comments ÷ all comments posted. A comment is relevant if it
          states the planted trap, or if the judge — shown the diff but not the
          trap — rates it a genuine defect; style notes and repeats are noise.
          Accent marks the leader in each column.
          {activeCase ? " Numbers below cover this case only." : ""}
        </Text>
        {unrated.length > 0 ? (
          <Callout tone="warning" title="Precision not comparable">
            <Text size="small">
              {unrated.join(", ")} used the fast judge or has not been rescored,
              so only the planted-trap comment counts as relevant. Run python3 -m
              prlab_eval rescore to rate every comment.
            </Text>
          </Callout>
        ) : null}
        <StandingsTable rows={standings} />
      </Stack>

      {/* all-cases view */}
      {!activeCase ? (
        <>
          <Stack gap={8}>
            <H2>Case by reviewer</H2>
            <Text size="small" tone="tertiary">
              One row per planted trap. “caught” means the judge confirmed the
              tool asserted the required finding and quoted real text from its
              own comment; the percentage is that case’s precision. Click a trap
              name to read the actual comments and open the PR.
            </Text>
            <Table
              stickyHeader
              striped
              headers={[
                "Trap",
                "Capability",
                "Caught by",
                ...tools.map((x) => x.tool),
              ]}
              rowTone={matrixRows.map((r) => r.tone)}
              rows={matrixRows.map((r) => [
                <div key="c" style={PASSTHROUGH}>
                  <span
                    onClick={() => setCaseId(r.meta.id)}
                    style={{
                      cursor: "pointer",
                      color: t.text.link,
                      fontSize: 12,
                      lineHeight: "16px",
                      fontWeight: 500,
                    }}
                  >
                    {shortCase(r.meta.id)}
                  </span>
                </div>,
                <div key="k" style={PASSTHROUGH}>
                  <Text size="small" tone="tertiary">
                    {r.meta.cap}
                  </Text>
                </div>,
                <div key="n" style={PASSTHROUGH}>
                  <Text size="small" weight="medium">
                    {r.caught}/{tools.length}
                  </Text>
                </div>,
                ...r.cells,
              ])}
            />
          </Stack>

          {hardest.length > 0 ? (
            <Stack gap={8}>
              <H2>Traps that beat at least one reviewer</H2>
              <Grid columns={2} gap={12}>
                {hardest.map((r) => {
                  const missed = tools
                    .filter((tr) => {
                      const run = tr.rows[r.meta.id];
                      return !run || !run.ok;
                    })
                    .map((tr) => tr.tool);
                  return (
                    <div key={r.meta.id} style={PASSTHROUGH}>
                      <Card>
                        <CardHeader
                          trailing={
                            <Text
                              size="small"
                              style={{
                                color:
                                  r.caught === 0
                                    ? t.category.red
                                    : t.category.yellow,
                              }}
                            >
                              {r.caught}/{tools.length} caught
                            </Text>
                          }
                        >
                          {shortCase(r.meta.id)}
                        </CardHeader>
                        <CardBody>
                          <Stack gap={6}>
                            <Text size="small" tone="secondary">
                              {r.meta.intent}
                            </Text>
                            <Text size="small" tone="quaternary">
                              Missed by: {missed.join(", ")}
                            </Text>
                          </Stack>
                        </CardBody>
                      </Card>
                    </div>
                  );
                })}
              </Grid>
            </Stack>
          ) : null}

          <Stack gap={8}>
            <H2>Recall by capability</H2>
            <Text size="small" tone="tertiary">
              Share of planted traps asserted, grouped by the reviewing skill
              each case measures. Y axis: recall (%). X axis: capability.
            </Text>
            <BarChart
              categories={capIds}
              series={tools.map((tr) => ({
                name: tr.tool,
                data: capIds.map((id) =>
                  Math.round((tr.caps[id]?.r ?? 0) * 100),
                ),
              }))}
              valueSuffix="%"
              yMax={100}
              height={280}
            />
            <Text size="small" tone="quaternary">
              Source: reports/&lt;tool&gt;/review-eval-*.json — latest run per
              tool, temperature-0 judge.
            </Text>
          </Stack>
        </>
      ) : null}

      {/* single-case view */}
      {activeCase ? (
        <Stack gap={16}>
          <Stack gap={8}>
            <H2>{shortCase(activeCase.id)}</H2>
            <Row gap={8} align="center" wrap>
              <Pill size="sm" active>
                {activeCase.cap}
              </Pill>
              <Text size="small" tone="quaternary">
                {activeCase.capid}
              </Text>
            </Row>
            <Text tone="secondary">{activeCase.intent}</Text>
          </Stack>

          <Callout tone="neutral" title="The trap">
            <Text size="small">{activeCase.tests}</Text>
          </Callout>

          <Stack gap={6}>
            <H3>What a correct review must assert</H3>
            {activeCase.claims.map((c) => (
              <div key={c.id} style={PASSTHROUGH}>
                <Text size="small" tone="secondary">
                  <Text as="span" size="small" weight="semibold">
                    {c.id}
                  </Text>
                  {" — "}
                  {c.must}
                </Text>
              </div>
            ))}
            <Text size="small" tone="quaternary">
              Capability asked of the tool: {activeCase.asks}
            </Text>
          </Stack>

          <Divider />

          <H3>How each reviewer did</H3>
          <Stack gap={12}>
            {tools.map((tr) => {
              const run = tr.rows[activeCase.id];
              if (!run) return null;
              return (
                <div key={tr.tool} style={PASSTHROUGH}>
                  <ToolDetailCard
                    run={run}
                    toolName={tr.tool}
                    meta={activeCase}
                  />
                </div>
              );
            })}
          </Stack>
        </Stack>
      ) : null}
    </Stack>
  );
}
