import type { CNode } from "./store";

export interface LayoutResult {
  positions: Record<string, { x: number; y: number }>;
  widths: Record<string, number>;
  heights: Record<string, number>;
}

const GAP_X = 40;
const GAP_Y = 32;
const MIN_LEVEL_H = 36 + GAP_Y; // collapsed node + gap = minimum height of one tree level

/** Pixel width of a node based on its state */
export function nodeW(n: CNode | undefined, expanded: boolean): number {
  if (!n) return 180;
  if (!n.parent_id && !n.user_message) return 260;
  if (expanded) return 340;
  return 180;
}

/** Pixel height of a node based on its state */
export function nodeH(n: CNode | undefined, expanded: boolean): number {
  if (!n) return 36;
  if (!n.parent_id && !n.user_message) return 80;
  if (expanded) return 240;
  return 36;
}

/**
 * Contour-based tree layout (Reingold–Tilford inspired).
 *
 * Instead of a single "subtree width" number, each subtree tracks its
 * left/right extent at every depth level. Siblings are only pushed apart
 * at depths where they'd actually overlap. This means expanding a deep
 * grandchild doesn't move shallow siblings that have no vertical overlap.
 *
 * Additionally, tall expanded nodes extend their contour to the depth
 * levels their body physically reaches, preventing overlap with nephew
 * nodes at those y-ranges.
 */
export function layoutTree(
  nodes: Record<string, CNode>,
  expandedNodes: Record<string, boolean>,
  measured?: Record<string, { width: number; height: number }>,
  collapsedSubtrees?: Record<string, boolean>,
): LayoutResult {
  const list = Object.values(nodes);
  const root = list.find((n) => !n.parent_id);
  if (!root) return { positions: {}, widths: {}, heights: {} };

  const children: Record<string, string[]> = {};
  for (const n of list) {
    if (collapsedSubtrees?.[n.id]) {
      // Don't add to children map — node appears as a leaf
    } else if (n.children_ids.length > 0) {
      children[n.id] = n.children_ids.filter((cid) => nodes[cid]);
    }
  }

  const w = (id: string) => measured?.[id]?.width ?? nodeW(nodes[id], !!expandedNodes[id]);
  const h = (id: string) => measured?.[id]?.height ?? nodeH(nodes[id], !!expandedNodes[id]);

  // ── Contour-based subtree shape ──────────────────────────────
  // left[d] / right[d] = leftmost / rightmost x extent at depth d,
  // relative to this subtree's root center-x.
  // childCxs[i] = center-x offset of child i relative to this node.

  interface SubtreeShape {
    left: number[];
    right: number[];
    childCxs: number[];
  }

  const shapes: Record<string, SubtreeShape> = {};

  function computeShape(id: string): SubtreeShape {
    const nw = w(id);
    const nh = h(id);
    const c = children[id] || [];

    // How many extra depth levels this node's body covers
    const extraLevels = Math.max(0, Math.ceil(nh / MIN_LEVEL_H) - 1);

    if (c.length === 0) {
      const left = Array(extraLevels + 1).fill(-nw / 2);
      const right = Array(extraLevels + 1).fill(nw / 2);
      const shape: SubtreeShape = { left, right, childCxs: [] };
      shapes[id] = shape;
      return shape;
    }

    // Recursively compute children
    const childShapes = c.map((cid) => computeShape(cid));

    // Place children left-to-right using contour comparison.
    // childCxs are absolute positions (first child at 0, re-centered later).
    const childCxs: number[] = [0];

    // Accumulated right contour across all placed children (absolute coords)
    let accRight = childShapes[0].right.slice();

    for (let i = 1; i < childShapes.length; i++) {
      const leftC = childShapes[i].left;
      let minPos = -Infinity;
      const maxD = Math.min(accRight.length, leftC.length);
      for (let d = 0; d < maxD; d++) {
        // Need: childCx + leftC[d] >= accRight[d] + GAP_X
        minPos = Math.max(minPos, accRight[d] - leftC[d] + GAP_X);
      }
      if (minPos === -Infinity) minPos = GAP_X;

      childCxs.push(minPos);

      // Update accumulated right contour
      const rightC = childShapes[i].right;
      for (let d = 0; d < rightC.length; d++) {
        const val = minPos + rightC[d];
        if (d < accRight.length) {
          accRight[d] = Math.max(accRight[d], val);
        } else {
          accRight.push(val);
        }
      }
    }

    // Compute combined left contour
    const combinedLeft: number[] = [];
    for (let i = 0; i < childShapes.length; i++) {
      const lc = childShapes[i].left;
      for (let d = 0; d < lc.length; d++) {
        const val = childCxs[i] + lc[d];
        if (d < combinedLeft.length) {
          combinedLeft[d] = Math.min(combinedLeft[d], val);
        } else {
          combinedLeft.push(val);
        }
      }
    }
    const combinedRight = accRight;

    // Center children under this node
    const mid = (combinedLeft[0] + combinedRight[0]) / 2;
    const centeredCxs = childCxs.map((cx) => cx - mid);
    const centeredLeft = combinedLeft.map((v) => v - mid);
    const centeredRight = combinedRight.map((v) => v - mid);

    // Build this node's contour.
    // Level 0: this node's own extent.
    // Level d (d >= 1): children's combined extent at level d-1.
    // For tall nodes, also include the node's own extent at levels 1..extraLevels.
    const maxChildLevel = centeredLeft.length;
    const totalLevels = Math.max(extraLevels + 1, 1 + maxChildLevel);
    const left: number[] = [];
    const right: number[] = [];

    for (let d = 0; d < totalLevels; d++) {
      let l = Infinity;
      let r = -Infinity;

      // This node's body covers levels 0..extraLevels
      if (d <= extraLevels) {
        l = Math.min(l, -nw / 2);
        r = Math.max(r, nw / 2);
      }

      // Children's combined contour (children start at level 1)
      if (d >= 1 && d - 1 < maxChildLevel) {
        l = Math.min(l, centeredLeft[d - 1]);
        r = Math.max(r, centeredRight[d - 1]);
      }

      left.push(l);
      right.push(r);
    }

    const shape: SubtreeShape = { left, right, childCxs: centeredCxs };
    shapes[id] = shape;
    return shape;
  }

  computeShape(root.id);

  // ── Position assignment ──────────────────────────────────────
  const positions: Record<string, { x: number; y: number }> = {};
  const widths: Record<string, number> = {};
  const heights: Record<string, number> = {};

  function place(id: string, cx: number, y: number) {
    const nw = w(id);
    const nh = h(id);
    // Convert center-x to top-left for React Flow
    positions[id] = { x: cx - nw / 2, y };
    widths[id] = nw;
    heights[id] = nh;

    const shape = shapes[id];
    const c = children[id] || [];
    const yStep = nh + GAP_Y;

    for (let i = 0; i < c.length; i++) {
      place(c[i], cx + shape.childCxs[i], y + yStep);
    }
  }

  place(root.id, 400, 40);

  return { positions, widths, heights };
}
