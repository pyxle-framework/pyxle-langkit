/**
 * Status bar management for the Pyxle Language Server.
 */

import * as vscode from "vscode";

export const enum StatusState {
    Starting,
    Running,
    Retrying,
    Failed,
    NotFound,
}

/**
 * Create and register a status bar item.
 */
export function createStatusBar(
    context: vscode.ExtensionContext,
): vscode.StatusBarItem {
    const item = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Left,
        0,
    );
    item.text = "$(loading~spin) Pyxle";
    item.tooltip = "Pyxle Language Server: starting...";
    item.show();
    context.subscriptions.push(item);
    return item;
}

/**
 * Update the status bar to reflect the current server state.
 */
export function updateStatus(
    item: vscode.StatusBarItem,
    state: StatusState,
    attempt?: number,
    maxRetries?: number,
): void {
    switch (state) {
        case StatusState.Starting:
            item.text = "$(loading~spin) Pyxle";
            item.tooltip = "Pyxle Language Server: starting...";
            break;
        case StatusState.Running:
            item.text = "$(check) Pyxle";
            item.tooltip = "Pyxle Language Server: running";
            break;
        case StatusState.Retrying:
            item.text = "$(warning) Pyxle";
            item.tooltip = `Pyxle Language Server: retrying (${attempt}/${maxRetries})...`;
            break;
        case StatusState.Failed:
            item.text = "$(error) Pyxle";
            item.tooltip =
                "Pyxle Language Server: failed to start. Check Output panel.";
            break;
        case StatusState.NotFound:
            item.text = "$(warning) Pyxle";
            item.tooltip =
                "Pyxle Language Server not found. Install via: pip install pyxle-langkit";
            item.command = "pyxle.showInstallGuide";
            break;
    }
}
