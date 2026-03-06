import { describe, it, expect } from "vitest";
import { layoutTree } from "./layout";
import type { CNode } from "./store";

function mkNode(
  id: string,
  parent_id: string | null,
  opts: Partial<CNode> = {},
): CNode {
  return {
    id,
    tree_id: "t1",
    parent_id,
    user_message: opts.user_message ?? (parent_id ? "question" : ""),
    assistant_response: opts.assistant_response ?? "",
    label: opts.label ?? id,
    status: opts.status ?? "done",
    children_ids: opts.children_ids ?? [],
    git_branch: opts.git_branch ?? null,
    git_commit: opts.git_commit ?? null,
    session_id: opts.session_id ?? null,
    created_by: opts.created_by ?? "human",
    ...opts,
  };
}

function toRecord(nodes: CNode[]): Record<string, CNode> {
  const r: Record<string, CNode> = {};
  for (const n of nodes) r[n.id] = n;
  return r;
}

/** Assert no two nodes have overlapping 2D bounding boxes */
function assertNoOverlap(result: ReturnType<typeof layoutTree>) {
  const entries = Object.entries(result.positions);
  for (let i = 0; i < entries.length; i++) {
    const [idA, posA] = entries[i];
    const wA = result.widths[idA];
    const hA = result.heights[idA];
    for (let j = i + 1; j < entries.length; j++) {
      const [idB, posB] = entries[j];
      const wB = result.widths[idB];
      const hB = result.heights[idB];

      const overlapX = posA.x < posB.x + wB && posB.x < posA.x + wA;
      const overlapY = posA.y < posB.y + hB && posB.y < posA.y + hA;

      if (overlapX && overlapY) {
        throw new Error(
          `Nodes ${idA} and ${idB} overlap!\n` +
            `  ${idA}: x=[${posA.x}, ${posA.x + wA}] y=[${posA.y}, ${posA.y + hA}]\n` +
            `  ${idB}: x=[${posB.x}, ${posB.x + wB}] y=[${posB.y}, ${posB.y + hB}]`,
        );
      }
    }
  }
}

/**
 * Assert that expanding a deep node doesn't move a shallow node at a
 * different y-level. Compares collapsed-all vs one-expanded layouts.
 */
function assertNoUnnecessaryMovement(
  nodes: Record<string, CNode>,
  expandId: string,
  stableId: string,
) {
  const collapsed = layoutTree(nodes, {});
  const expanded = layoutTree(nodes, { [expandId]: true });

  const before = collapsed.positions[stableId];
  const after = expanded.positions[stableId];

  if (before.x !== after.x || before.y !== after.y) {
    throw new Error(
      `Expanding ${expandId} moved ${stableId} from (${before.x},${before.y}) to (${after.x},${after.y})`,
    );
  }
}

describe("layoutTree", () => {
  it("returns empty for no nodes", () => {
    const result = layoutTree({}, {});
    expect(Object.keys(result.positions)).toHaveLength(0);
  });

  it("places a single root", () => {
    const nodes = toRecord([mkNode("r", null)]);
    const result = layoutTree(nodes, {});
    expect(result.positions["r"]).toBeDefined();
  });

  it("two collapsed siblings don't overlap", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r"),
    ]);
    assertNoOverlap(layoutTree(nodes, {}));
  });

  it("two expanded siblings don't overlap", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r"),
    ]);
    assertNoOverlap(layoutTree(nodes, { a: true, b: true }));
  });

  it("one expanded + one collapsed sibling don't overlap", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r"),
    ]);
    assertNoOverlap(layoutTree(nodes, { a: true }));
  });

  it("three siblings, middle expanded, don't overlap", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b", "c"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r"),
      mkNode("c", "r"),
    ]);
    assertNoOverlap(layoutTree(nodes, { b: true }));
  });

  it("expanded parent with expanded children don't overlap", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r", { children_ids: ["a1", "a2"] }),
      mkNode("b", "r"),
      mkNode("a1", "a"),
      mkNode("a2", "a"),
    ]);
    assertNoOverlap(layoutTree(nodes, { r: true, a: true, a1: true, a2: true, b: true }));
  });

  it("deep tree with mixed expand states", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r", { children_ids: ["a1"] }),
      mkNode("b", "r", { children_ids: ["b1", "b2"] }),
      mkNode("a1", "a", { children_ids: ["a1x"] }),
      mkNode("b1", "b"),
      mkNode("b2", "b"),
      mkNode("a1x", "a1"),
    ]);
    assertNoOverlap(layoutTree(nodes, { a: true, a1: true, b2: true }));
  });

  it("expanded root with message doesn't overlap children", () => {
    const nodes = toRecord([
      mkNode("r", null, {
        children_ids: ["a"],
        user_message: "hello",
        assistant_response: "world",
      }),
      mkNode("a", "r"),
    ]);
    assertNoOverlap(layoutTree(nodes, { r: true }));
  });

  it("four siblings all expanded don't overlap", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b", "c", "d"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r"),
      mkNode("c", "r"),
      mkNode("d", "r"),
    ]);
    assertNoOverlap(layoutTree(nodes, { a: true, b: true, c: true, d: true }));
  });

  it("cousin nodes at same depth don't overlap", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r", { children_ids: ["a1", "a2"] }),
      mkNode("b", "r", { children_ids: ["b1", "b2"] }),
      mkNode("a1", "a"),
      mkNode("a2", "a"),
      mkNode("b1", "b"),
      mkNode("b2", "b"),
    ]);
    assertNoOverlap(layoutTree(nodes, { a2: true, b1: true }));
  });

  // ── Bug report: expanding grandchild moves unrelated top-left node ──

  it("expanding a grandchild does NOT move a collapsed sibling at depth 1", () => {
    // Tree:     r
    //          / \
    //         a   b
    //             |
    //             b1 (will be expanded)
    // Expanding b1 should NOT move 'a'.
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r", { children_ids: ["b1"] }),
      mkNode("b1", "b"),
    ]);
    assertNoUnnecessaryMovement(nodes, "b1", "a");
  });

  it("expanding a deep-right grandchild does NOT move top-left nodes", () => {
    // Tree:      r
    //          / | \
    //         a  b  c
    //               |
    //               c1 (will be expanded)
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b", "c"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r"),
      mkNode("c", "r", { children_ids: ["c1"] }),
      mkNode("c1", "c"),
    ]);
    assertNoUnnecessaryMovement(nodes, "c1", "a");
    assertNoUnnecessaryMovement(nodes, "c1", "b");
  });

  // ── Tall expanded node overlapping with nephew nodes ──

  it("tall expanded node doesn't overlap nephew at overlapping y-range", () => {
    // Tree:     r
    //          / \
    //         a   b
    //             |
    //             b1
    // 'a' expanded = 240px tall, b1 at y ~ a.y + 76 (within a's y-range)
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r", { children_ids: ["b1"] }),
      mkNode("b1", "b"),
    ]);
    assertNoOverlap(layoutTree(nodes, { a: true }));
  });

  it("tall expanded node doesn't overlap expanded nephew", () => {
    // Both a (expanded, tall) and b1 (expanded, wide) should not overlap
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r"),
      mkNode("b", "r", { children_ids: ["b1"] }),
      mkNode("b1", "b"),
    ]);
    assertNoOverlap(layoutTree(nodes, { a: true, b1: true }));
  });

  it("root hub (no message) doesn't overlap children", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"] }),
      mkNode("a", "r"),
      mkNode("b", "r"),
    ]);
    assertNoOverlap(layoutTree(nodes, {}));
    assertNoOverlap(layoutTree(nodes, { a: true }));
    assertNoOverlap(layoutTree(nodes, { a: true, b: true }));
  });

  it("all nodes expanded in a 3-level tree", () => {
    const nodes = toRecord([
      mkNode("r", null, { children_ids: ["a", "b"], user_message: "q" }),
      mkNode("a", "r", { children_ids: ["a1", "a2"] }),
      mkNode("b", "r", { children_ids: ["b1", "b2"] }),
      mkNode("a1", "a"),
      mkNode("a2", "a"),
      mkNode("b1", "b"),
      mkNode("b2", "b"),
    ]);
    assertNoOverlap(
      layoutTree(nodes, { r: true, a: true, b: true, a1: true, a2: true, b1: true, b2: true }),
    );
  });
});
