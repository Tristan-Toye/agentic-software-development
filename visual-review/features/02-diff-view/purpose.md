# Purpose

The owner asked for "a git diff of the diagram". GitHub shows no structural diff of a diagram, and a search for a Mermaid diff in PRs found nothing ([review-ux-and-feedback](../../research/synthesis/review-ux-and-feedback.md), section 2). The diff must be built.

VR-02 builds it as one merged graph with one status word per element, so that one layout serves all three modes. This avoids the layout-stability problem that no surveyed tool solves ([diagram-diff-approaches](../../research/sources/diagram-diff-approaches.md)).
