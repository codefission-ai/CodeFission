import { describe, it, expect } from "vitest";
import { isDagLeaf, type CNode } from "./store";

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

describe("isDagLeaf", () => {
  //  Tree:  root -> A -> B
  const root = makeNode("root", null, ["A"]);
  const A = makeNode("A", "root", ["B"]);
  const B = makeNode("B", "A", []);
  const simple = toMap([root, A, B]);
  const empty = new Set<string>();

  it("leaf node with no children and no quotes is a leaf", () => {
    expect(isDagLeaf(simple, "B", empty)).toBe(true);
  });

  it("node with children is not a leaf", () => {
    expect(isDagLeaf(simple, "A", empty)).toBe(false);
    expect(isDagLeaf(simple, "root", empty)).toBe(false);
  });

  it("root is never a leaf (has children)", () => {
    expect(isDagLeaf(simple, "root", empty)).toBe(false);
  });

  // Tree: root -> A -> B, A quotes B
  it("node quoted by another node is not a leaf", () => {
    const Aq = makeNode("A", "root", ["B"], ["B"]);
    const nodes = toMap([root, Aq, B]);
    expect(isDagLeaf(nodes, "B", empty)).toBe(false); // A quotes B
  });

  // Tree: root -> A -> B, C quotes B
  it("node quoted by a sibling is not a leaf", () => {
    const rootWide = makeNode("root", null, ["A", "C"]);
    const C = makeNode("C", "root", [], ["B"]);
    const nodes = toMap([rootWide, A, B, C]);
    expect(isDagLeaf(nodes, "B", empty)).toBe(false); // C quotes B
    expect(isDagLeaf(nodes, "C", empty)).toBe(true);  // nothing depends on C
  });

  // Note referenced by a node
  it("note quoted by a node is not a leaf", () => {
    const Aq = makeNode("A", "root", [], ["note-1"]);
    const nodes = toMap([root, Aq]);
    expect(isDagLeaf(nodes, "note-1", empty)).toBe(false);
  });

  it("note not quoted by any node is a leaf", () => {
    expect(isDagLeaf(simple, "note-1", empty)).toBe(true);
  });

  // Pending-delete nodes don't count as dependents
  it("pending-delete child does not block parent from being leaf", () => {
    const pending = new Set(["B"]);
    expect(isDagLeaf(simple, "A", pending)).toBe(true);
  });

  it("pending-delete quoter does not block quoted node from being leaf", () => {
    const Aq = makeNode("A", "root", ["B"], ["note-1"]);
    const nodes = toMap([root, Aq, B]);
    const pending = new Set(["A"]);
    expect(isDagLeaf(nodes, "note-1", pending)).toBe(true);
  });

  it("only some dependents pending-delete: still not a leaf", () => {
    const rootWide = makeNode("root", null, ["A", "C"]);
    const C = makeNode("C", "root", [], ["B"]);
    const nodes = toMap([rootWide, A, B, C]);
    // C quotes B, only A is pending-delete, C is not
    const pending = new Set(["A"]);
    expect(isDagLeaf(nodes, "B", pending)).toBe(false); // C still quotes B
  });

  // Chain delete: delete B -> A becomes leaf -> delete A -> root's only child gone
  it("chain deletion: deleting leaf exposes parent as new leaf", () => {
    const pending1 = new Set(["B"]);
    expect(isDagLeaf(simple, "B", empty)).toBe(true);  // B is leaf
    expect(isDagLeaf(simple, "A", empty)).toBe(false);  // A has child B
    expect(isDagLeaf(simple, "A", pending1)).toBe(true); // B pending -> A is leaf

    const pending2 = new Set(["B", "A"]);
    // root has child A but A is pending-delete
    expect(isDagLeaf(simple, "root", pending2)).toBe(true);
  });

  // Multiple quotes: node quoted by two nodes, one pending-delete
  it("node quoted by multiple: only leaf if ALL quoters are pending-delete", () => {
    const Aq = makeNode("A", "root", [], ["B"]);
    const Cq = makeNode("C", "root", [], ["B"]);
    const Bleaf = makeNode("B", "root", []);
    const rootB = makeNode("root", null, ["A", "C", "B"]);
    const nodes = toMap([rootB, Aq, Cq, Bleaf]);

    expect(isDagLeaf(nodes, "B", empty)).toBe(false);  // A and C both quote B
    expect(isDagLeaf(nodes, "B", new Set(["A"]))).toBe(false); // C still quotes
    expect(isDagLeaf(nodes, "B", new Set(["A", "C"]))).toBe(true); // all quoters gone
  });

  // Self-quote edge case: node quoting itself should not prevent deletion
  // (a node is never its own dependent in a meaningful sense)
  // Actually isDagLeaf iterates all nodes including self — but since n.id === id
  // we'd say n depends on id. This is technically correct (self-ref is a cycle),
  // but let's verify the behavior is at least consistent:
  it("self-quoting node: self-ref counts as dependency", () => {
    const selfRef = makeNode("A", "root", [], ["A"]);
    const nodes = toMap([root, selfRef]);
    // A quotes itself — isDagLeaf returns false because A references A
    expect(isDagLeaf(nodes, "A", empty)).toBe(false);
    // But if A is pending-delete, the self-ref is skipped
    expect(isDagLeaf(nodes, "A", new Set(["A"]))).toBe(true);
  });
});
