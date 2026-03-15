import { describe, it, expect } from "vitest";
import { isDetachable, type CNode } from "./store";

function makeNode(id: string, parent_id: string | null, children_ids: string[] = [], quoted_node_ids: string[] = []): CNode {
  return {
    id,
    tree_id: "t1",
    parent_id,
    user_message: "",
    assistant_response: "",
    label: id,
    status: "done",
    children_ids,
    git_branch: null,
    git_commit: null,
    created_by: "human",
    quoted_node_ids,
  };
}

function toMap(nodes: CNode[]): Record<string, CNode> {
  const m: Record<string, CNode> = {};
  for (const n of nodes) m[n.id] = n;
  return m;
}

describe("isDetachable", () => {
  //  Tree:  root -> A -> B
  const root = makeNode("root", null, ["A"]);
  const A = makeNode("A", "root", ["B"]);
  const B = makeNode("B", "A", []);
  const simple = toMap([root, A, B]);
  const empty = new Set<string>();

  it("leaf node with no children and no quotes is detachable", () => {
    expect(isDetachable(simple, "B", empty)).toBe(true);
  });

  it("node with children but no external quotes is detachable (subtree delete)", () => {
    // A has child B, but nothing outside A's subtree quotes B → detachable
    expect(isDetachable(simple, "A", empty)).toBe(true);
  });

  it("root with children is detachable (but UI prevents root deletion)", () => {
    expect(isDetachable(simple, "root", empty)).toBe(true);
  });

  // Tree: root -> A -> B, A quotes B (internal quote within subtree)
  it("internal quote within subtree does not prevent detachment", () => {
    const Aq = makeNode("A", "root", ["B"], ["B"]);
    const nodes = toMap([root, Aq, B]);
    // A quotes B, but both are in A's subtree → detachable
    expect(isDetachable(nodes, "A", empty)).toBe(true);
  });

  // Tree: root -> {A -> B, C quotes B}
  it("node whose child is quoted by external node is not detachable", () => {
    const rootWide = makeNode("root", null, ["A", "C"]);
    const C = makeNode("C", "root", [], ["B"]);
    const nodes = toMap([rootWide, A, B, C]);
    // C (external) quotes B (inside A's subtree) → A is NOT detachable
    expect(isDetachable(nodes, "A", empty)).toBe(false);
    // B itself is not detachable either (C quotes it)
    expect(isDetachable(nodes, "B", empty)).toBe(false);
    // C is detachable (nothing quotes it)
    expect(isDetachable(nodes, "C", empty)).toBe(true);
  });

  // Note referenced by a node
  it("note quoted by a node is not detachable", () => {
    const Aq = makeNode("A", "root", [], ["note-1"]);
    const nodes = toMap([root, Aq]);
    expect(isDetachable(nodes, "note-1", empty)).toBe(false);
  });

  it("note not quoted by any node is detachable", () => {
    expect(isDetachable(simple, "note-1", empty)).toBe(true);
  });

  // Pending-delete nodes don't count as dependents
  it("pending-delete child does not block parent from being detachable", () => {
    const pending = new Set(["B"]);
    expect(isDetachable(simple, "A", pending)).toBe(true);
  });

  it("pending-delete quoter does not block quoted node from being detachable", () => {
    const Aq = makeNode("A", "root", ["B"], ["note-1"]);
    const nodes = toMap([root, Aq, B]);
    const pending = new Set(["A"]);
    expect(isDetachable(nodes, "note-1", pending)).toBe(true);
  });

  it("only some external dependents pending-delete: still not detachable", () => {
    const rootWide = makeNode("root", null, ["A", "C", "D"]);
    const C = makeNode("C", "root", [], ["B"]);
    const D = makeNode("D", "root", [], ["B"]);
    const nodes = toMap([rootWide, A, B, C, D]);
    // C and D both quote B. Only C is pending → D still blocks
    const pending = new Set(["C"]);
    expect(isDetachable(nodes, "B", pending)).toBe(false);
  });

  // Chain delete
  it("chain deletion: deleting leaf exposes parent as detachable", () => {
    const pending1 = new Set(["B"]);
    expect(isDetachable(simple, "B", empty)).toBe(true);
    expect(isDetachable(simple, "A", pending1)).toBe(true);

    const pending2 = new Set(["B", "A"]);
    expect(isDetachable(simple, "root", pending2)).toBe(true);
  });

  // Subtree with deep external quote
  it("deep external quote prevents detachment", () => {
    // root -> A -> B -> C, and D quotes C
    const rootD = makeNode("root", null, ["A", "D"]);
    const Ad = makeNode("A", "root", ["B"]);
    const Bd = makeNode("B", "A", ["C"]);
    const Cd = makeNode("C", "B", []);
    const Dq = makeNode("D", "root", [], ["C"]);
    const nodes = toMap([rootD, Ad, Bd, Cd, Dq]);
    // A's subtree = {A, B, C}. D (outside) quotes C → not detachable
    expect(isDetachable(nodes, "A", empty)).toBe(false);
    // B's subtree = {B, C}. D quotes C → not detachable
    expect(isDetachable(nodes, "B", empty)).toBe(false);
    // C itself is quoted by D → not detachable
    expect(isDetachable(nodes, "C", empty)).toBe(false);
    // D is detachable
    expect(isDetachable(nodes, "D", empty)).toBe(true);
    // If D is pending-delete, A becomes detachable
    expect(isDetachable(nodes, "A", new Set(["D"]))).toBe(true);
  });

  // Internal cross-quotes within subtree are fine
  it("cross-quotes within subtree do not prevent detachment", () => {
    // root -> A -> {B, C}, B quotes C (both inside A's subtree)
    const rootA = makeNode("root", null, ["A"]);
    const Ax = makeNode("A", "root", ["B", "C"]);
    const Bq = makeNode("B", "A", [], ["C"]);
    const Cx = makeNode("C", "A", []);
    const nodes = toMap([rootA, Ax, Bq, Cx]);
    expect(isDetachable(nodes, "A", empty)).toBe(true);
  });

  // Multiple quotes: node quoted by two external nodes
  it("node quoted by multiple externals: only detachable if ALL quoters pending-delete", () => {
    const Aq = makeNode("A", "root", [], ["B"]);
    const Cq = makeNode("C", "root", [], ["B"]);
    const Bleaf = makeNode("B", "root", []);
    const rootB = makeNode("root", null, ["A", "C", "B"]);
    const nodes = toMap([rootB, Aq, Cq, Bleaf]);

    expect(isDetachable(nodes, "B", empty)).toBe(false);
    expect(isDetachable(nodes, "B", new Set(["A"]))).toBe(false);
    expect(isDetachable(nodes, "B", new Set(["A", "C"]))).toBe(true);
  });

  // Self-quote edge case
  it("self-quoting node: self-ref counts as dependency (node is in its own subtree so it's fine)", () => {
    const selfRef = makeNode("A", "root", [], ["A"]);
    const nodes = toMap([root, selfRef]);
    // A quotes itself — but A is inside its own subtree → detachable
    expect(isDetachable(nodes, "A", empty)).toBe(true);
  });
});
