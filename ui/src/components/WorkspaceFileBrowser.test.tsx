// @vitest-environment jsdom

import type { ComponentProps } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import type { Project, ProjectWorkspace, WorkspaceFileListItem, WorkspaceFileListResponse } from "@paperclipai/shared";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceFileBrowser, describeUnavailable } from "./WorkspaceFileBrowser";

function act(callback: () => void | Promise<void>) {
  let result: void | Promise<void> | undefined;
  flushSync(() => {
    result = callback();
  });
  return result;
}

const useQueryMock = vi.fn();

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query");
  return {
    ...actual,
    useQuery: (options: unknown) => useQueryMock(options),
  };
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

function createItem(overrides: Partial<WorkspaceFileListItem> = {}): WorkspaceFileListItem {
  return {
    kind: "file",
    provider: "git_worktree",
    title: "IssueDetail.tsx",
    relativePath: "ui/src/pages/IssueDetail.tsx",
    displayPath: "ui/src/pages/IssueDetail.tsx",
    workspaceLabel: "Isolated workspace",
    workspaceKind: "execution_workspace",
    workspaceId: "ws-1",
    contentType: "text/plain; charset=utf-8",
    byteSize: 2048,
    modifiedAt: new Date(Date.now() - 120_000).toISOString(),
    previewKind: "text",
    capabilities: { preview: true, download: false, listChildren: false },
    ...overrides,
  };
}

function availableResponse(items: WorkspaceFileListItem[], truncated = false): WorkspaceFileListResponse {
  return {
    kind: "workspace_file_list",
    state: "available",
    workspace: {
      provider: "git_worktree",
      workspaceLabel: "Isolated workspace",
      workspaceKind: "execution_workspace",
      workspaceId: "ws-1",
    },
    query: { workspace: "auto", mode: "changed", q: null, limit: 100 },
    items,
    scannedCount: items.length,
    truncated,
  };
}

function createWorkspace(overrides: Partial<ProjectWorkspace> = {}): ProjectWorkspace {
  return {
    id: "workspace-content",
    companyId: "company-1",
    projectId: "project-content",
    name: "Paperclip Content",
    sourceType: "local_path",
    cwd: "/srv/paperclip/home/paperclipai/paperclip-content",
    repoUrl: null,
    repoRef: null,
    defaultRef: null,
    visibility: "default",
    setupCommand: null,
    cleanupCommand: null,
    remoteProvider: null,
    remoteWorkspaceRef: null,
    sharedWorkspaceKey: null,
    metadata: null,
    runtimeConfig: null,
    isPrimary: true,
    createdAt: new Date("2026-06-08T00:00:00.000Z"),
    updatedAt: new Date("2026-06-08T00:00:00.000Z"),
    ...overrides,
  };
}

function createProject(overrides: Partial<Project> = {}): Project {
  const workspace = createWorkspace();
  return {
    id: "project-content",
    companyId: "company-1",
    urlKey: "paperclip-content",
    goalId: null,
    goalIds: [],
    goals: [],
    name: "Paperclip Content",
    description: null,
    status: "in_progress",
    leadAgentId: null,
    targetDate: null,
    color: null,
    icon: null,
    env: null,
    pauseReason: null,
    pausedAt: null,
    executionWorkspacePolicy: null,
    codebase: {
      workspaceId: workspace.id,
      repoUrl: null,
      repoRef: null,
      defaultRef: null,
      repoName: null,
      localFolder: workspace.cwd,
      managedFolder: "",
      effectiveLocalFolder: workspace.cwd ?? "",
      origin: "local_folder",
    },
    workspaces: [workspace],
    primaryWorkspace: workspace,
    archivedAt: null,
    createdAt: new Date("2026-06-08T00:00:00.000Z"),
    updatedAt: new Date("2026-06-08T00:00:00.000Z"),
    ...overrides,
  };
}

function unavailableResponse(reason: string): WorkspaceFileListResponse {
  return {
    kind: "workspace_file_list",
    state: "unavailable",
    unavailableReason: reason,
    workspace: null,
    query: { workspace: "auto", mode: "changed", q: null, limit: 100 },
    items: [],
    scannedCount: 0,
    truncated: false,
  };
}

function ok<T>(data: T) {
  return { data, isFetching: false, isError: false, error: null, refetch: vi.fn() };
}

describe("WorkspaceFileBrowser", () => {
  let container: HTMLDivElement;
  const roots: Root[] = [];

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    useQueryMock.mockReset();
  });

  afterEach(() => {
    for (const root of roots.splice(0)) {
      act(() => {
        root.unmount();
      });
    }
    container.remove();
  });

  function renderBrowser(onOpen = vi.fn(), props: Partial<ComponentProps<typeof WorkspaceFileBrowser>> = {}) {
    const root = createRoot(container);
    roots.push(root);
    act(() => {
      root.render(<WorkspaceFileBrowser issueId="issue-1" onOpen={onOpen} {...props} />);
    });
    return { root, onOpen };
  }

  it("renders the Recently changed files as a tree and opens a row with its relative path", () => {
    useQueryMock.mockReturnValue(
      ok(availableResponse([createItem(), createItem({ relativePath: "README.md", displayPath: "README.md" })])),
    );

    const { onOpen } = renderBrowser();

    expect(container.querySelector('[role="tree"]')).not.toBeNull();
    expect(container.textContent).toContain("Recently changed");
    expect(container.textContent).toContain("From Isolated workspace");

    const option = Array.from(container.querySelectorAll('[role="treeitem"]')).find(
      (el) => el.getAttribute("title") === "ui/src/pages/IssueDetail.tsx",
    );
    expect(option).not.toBeUndefined();

    act(() => {
      option!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(onOpen).toHaveBeenCalledWith({ path: "ui/src/pages/IssueDetail.tsx", workspace: "auto" });
  });

  it("groups nested paths into collapsible folder rows instead of repeating full paths", () => {
    useQueryMock.mockReturnValue(
      ok(availableResponse([
        createItem({
          title: "tweet.md",
          relativePath: "videos/90-days-paperclip/tweet.md",
          displayPath: "videos/90-days-paperclip/tweet.md",
        }),
        createItem({
          title: "90-days-paperclip-1x1.mp4",
          relativePath: "videos/90-days-paperclip/out/90-days-paperclip-1x1.mp4",
          displayPath: "videos/90-days-paperclip/out/90-days-paperclip-1x1.mp4",
          previewKind: "video",
        }),
      ])),
    );

    renderBrowser();

    expect(container.querySelector('[role="tree"]')).not.toBeNull();
    expect(container.textContent).toContain("videos");
    expect(container.textContent).toContain("90-days-paperclip");
    expect(container.textContent).toContain("tweet.md");
    expect(container.textContent).toContain("90-days-paperclip-1x1.mp4");
    expect(container.textContent).not.toContain("videos/90-days-paperclip/tweet.md");

    const videosFolder = Array.from(container.querySelectorAll('button[role="treeitem"]')).find(
      (el) => el.getAttribute("title") === "videos",
    );
    expect(videosFolder).not.toBeUndefined();
    act(() => {
      videosFolder!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(container.textContent).not.toContain("tweet.md");
  });

  it("can render without autofocus when embedded beside a preview", () => {
    useQueryMock.mockReturnValue(ok(availableResponse([createItem()])));
    renderBrowser(vi.fn(), { autoFocusSearch: false });
    expect(container.querySelector("input")?.hasAttribute("autofocus")).toBe(false);
  });

  it("does not render a Recent/All toggle", () => {
    useQueryMock.mockReturnValue(ok(availableResponse([createItem()])));
    renderBrowser();
    expect(container.textContent).not.toContain("All files");
    expect(container.textContent).not.toContain("Recent changes / All");
  });

  it("discloses truncation in the footer", () => {
    useQueryMock.mockReturnValue(ok(availableResponse([createItem()], true)));
    renderBrowser();
    expect(container.textContent).toContain("refine the search to narrow");
  });

  it("opens the highlighted row when Enter is pressed in the search field", () => {
    useQueryMock.mockReturnValue(ok(availableResponse([createItem()])));
    const { onOpen } = renderBrowser();
    const input = container.querySelector("input")!;
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    });
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });
    expect(onOpen).toHaveBeenCalledWith({ path: "ui/src/pages/IssueDetail.tsx", workspace: "auto" });
  });

  it("shows the remote-workspace state without file rows", () => {
    useQueryMock.mockReturnValue(ok(unavailableResponse("remote_workspace")));
    renderBrowser();
    expect(container.textContent).toContain("Remote workspace preview not supported");
    expect(container.querySelector('[role="tree"]')).toBeNull();
  });

  it("shows the no-workspace state when the issue has no workspace", () => {
    useQueryMock.mockReturnValue(ok(unavailableResponse("no_workspace")));
    renderBrowser();
    expect(container.textContent).toContain("No workspace yet");
  });

  it("opens a result from a selected other project workspace", () => {
    const contentItem = createItem({
      relativePath: "content-os/cases/active/2026-06-06-pap-10199-bundled-skills/README.md",
      displayPath: "Paperclip Content / content-os/cases/active/2026-06-06-pap-10199-bundled-skills/README.md",
      workspaceLabel: "Paperclip Content",
      workspaceKind: "project_workspace",
      workspaceId: "workspace-content",
      projectId: "project-content",
      projectName: "Paperclip Content",
    });
    useQueryMock.mockImplementation((options: { queryKey: readonly unknown[] }) => {
      if (options.queryKey[0] === "projects") return ok([createProject()]);
      return ok(availableResponse([contentItem]));
    });

    const { onOpen } = renderBrowser(vi.fn(), {
      companyId: "company-1",
      initialProjectId: "project-content",
      initialWorkspaceId: "workspace-content",
    });

    expect(container.textContent).toContain("Other project");
    expect(container.textContent).toContain("Browsing Paperclip Content / Paperclip Content");
    const listCall = useQueryMock.mock.calls.find(([options]) => options.queryKey?.[3] === "list");
    expect(listCall?.[0].queryKey[4]).toMatchObject({
      workspace: "project",
      projectId: "project-content",
      workspaceId: "workspace-content",
    });

    const option = Array.from(container.querySelectorAll('[role="treeitem"]')).find(
      (el) => el.getAttribute("title") === contentItem.displayPath,
    )!;
    act(() => {
      option.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(onOpen).toHaveBeenCalledWith({
      path: "content-os/cases/active/2026-06-06-pap-10199-bundled-skills/README.md",
      workspace: "project",
      projectId: "project-content",
      workspaceId: "workspace-content",
    });
  });

  it("focuses an initial folder in a selected other project workspace", () => {
    const folderPath = "content-os/cases/active/2026-06-06-pap-10199-bundled-skills/";
    const contentItem = createItem({
      relativePath: `${folderPath}README.md`,
      displayPath: `Paperclip Content / ${folderPath}README.md`,
      workspaceLabel: "Paperclip Content",
      workspaceKind: "project_workspace",
      workspaceId: "workspace-content",
      projectId: "project-content",
      projectName: "Paperclip Content",
    });
    useQueryMock.mockImplementation((options: { queryKey: readonly unknown[] }) => {
      if (options.queryKey[0] === "projects") return ok([createProject()]);
      return ok(availableResponse([contentItem]));
    });

    const { onOpen } = renderBrowser(vi.fn(), {
      companyId: "company-1",
      initialProjectId: "project-content",
      initialWorkspaceId: "workspace-content",
      initialFolderPath: folderPath,
    });

    expect(container.textContent).toContain("bundled-skills");
    expect(container.textContent).not.toContain(folderPath);
    expect(container.textContent).toContain("Files in folder");
    const listCall = useQueryMock.mock.calls.find(([options]) => options.queryKey?.[3] === "list");
    expect(listCall?.[0].queryKey[4]).toMatchObject({
      workspace: "project",
      projectId: "project-content",
      workspaceId: "workspace-content",
      path: folderPath,
    });

    const option = Array.from(container.querySelectorAll('[role="treeitem"]')).find(
      (el) => el.getAttribute("title") === contentItem.displayPath,
    )!;
    act(() => {
      option.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    expect(onOpen).toHaveBeenCalledWith({
      path: `${folderPath}README.md`,
      workspace: "project",
      projectId: "project-content",
      workspaceId: "workspace-content",
    });
  });
});

describe("describeUnavailable", () => {
  it("maps reasons to copy that matches the viewer's denial voice", () => {
    expect(describeUnavailable("remote_workspace").title).toBe("Remote workspace preview not supported");
    expect(describeUnavailable("no_workspace").title).toBe("No workspace yet");
    expect(describeUnavailable("no_local_workspace").title).toBe("No workspace yet");
    expect(describeUnavailable("workspace_unavailable").title).toBe("Workspace is no longer available");
    expect(describeUnavailable("archived").title).toBe("Workspace is no longer available");
  });

  it("never leaks the raw reason code as the body", () => {
    for (const reason of ["remote_workspace", "no_workspace", "workspace_unavailable", "weird_unknown"]) {
      const { body } = describeUnavailable(reason);
      expect(body).not.toBe(reason);
      expect(body).not.toMatch(/^[a-z_]+$/);
    }
  });
});
