bd - Dependency-Aware Issue Tracker

Issues chained together like beads.

GETTING STARTED
  bd init   Initialize bd in your project (embedded Dolt, no server needed)
            Creates .beads/ directory with project-specific Dolt database
            Auto-detects prefix from directory name (e.g., myapp-1, myapp-2)

  bd init --prefix api   Initialize with custom prefix
            Issues will be named: api-<hash> (e.g., api-a3f2dd)

  bd init --server   Initialize with external Dolt server (multi-writer)
            Connects to a running dolt sql-server for concurrent access

CREATING ISSUES
  bd create "Fix login bug"
  bd create "Add auth" -p 0 -t feature
  bd create "Write tests" -d "Unit tests for auth" --assignee alice

VIEWING ISSUES
  bd list       List all issues
  bd list --status open  List by status
  bd list --priority 0  List by priority (0-4, 0=highest)
  bd show bd-1       Show issue details

MANAGING DEPENDENCIES
  bd dep add bd-1 bd-2     Add dependency (bd-2 blocks bd-1)
  bd dep tree bd-1  Visualize dependency tree
  bd dep cycles      Detect circular dependencies

DEPENDENCY TYPES
  blocks  Task B must complete before task A
  related  Soft connection, doesn't block progress
  parent-child  Epic/subtask hierarchical relationship
  discovered-from  Auto-created when AI discovers related work

READY WORK
  bd ready       Show issues ready to work on
            Ready = status is 'open' AND no blocking dependencies
            Perfect for agents to claim next work!

UPDATING ISSUES
  bd update bd-1 --claim
  bd update bd-1 --priority 0
  bd update bd-1 --assignee bob

CLOSING ISSUES
  bd close bd-1
  bd close bd-2 bd-3 --reason "Fixed in PR #42"

STORAGE
  bd uses Dolt, a version-controlled SQL database:
    ●  Embedded mode (default): in-process, zero config
              Data stored in .beads/embeddeddolt/
    ●  Server mode (bd init --server): multi-writer via dolt sql-server
              Data managed by external server

SYNC
  Share issues with your team using Dolt remotes:
    bd dolt remote add origin git+ssh://git@github.com/org/repo.git  Add remote
    bd dolt push              Push issues
    bd dolt pull              Pull from teammates
  Dolt handles sync natively with cell-level merge — no manual export needed

AGENT INTEGRATION
  bd is designed for AI-supervised workflows:
    ● Agents create issues when discovering new work
    ● bd ready shows unblocked work ready to claim
    ● Use --json flags for programmatic parsing
    ● Dependencies prevent agents from duplicating effort

Ready to start!
Run bd create "My first issue" to create your first issue.