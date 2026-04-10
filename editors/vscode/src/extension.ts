/**
 * Pyxle Language Tools — VS Code extension.
 *
 * This is a thin LSP client.  All language intelligence (completions, hover,
 * diagnostics, formatting, semantic tokens, go-to-definition) is provided by
 * the ``pyxle-langserver`` Python process.  The extension's only job is to
 * launch that process, wire up the Language Client, and manage the status bar.
 *
 * JSX intelligence comes from two sources:
 * 1. The LSP server provides Pyxle-specific completions (components, props)
 * 2. The ``embeddedLanguages`` mapping in the grammar contribution tells
 *    VS Code that ``source.js.jsx`` regions are ``javascriptreact``, which
 *    enables basic word-based completions and bracket matching from VS Code's
 *    built-in TypeScript/JavaScript extension.
 *
 * Full semantic JSX intelligence (type-aware completions, go-to-definition
 * into React types) requires embedding TypeScript's language service in the
 * LSP server — planned for a future release.
 */

import * as vscode from "vscode";
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
} from "vscode-languageclient/node";
import { createStatusBar, updateStatus, StatusState } from "./status";

const LANGUAGE_ID = "pyxle";
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2_000;

let client: LanguageClient | undefined;
let outputChannel: vscode.OutputChannel;
let statusBar: vscode.StatusBarItem;

/* ------------------------------------------------------------------ */
/*  Activation                                                        */
/* ------------------------------------------------------------------ */

export function activate(context: vscode.ExtensionContext): void {
    outputChannel = vscode.window.createOutputChannel("Pyxle Language Server");
    context.subscriptions.push(outputChannel);

    statusBar = createStatusBar(context);
    updateStatus(statusBar, StatusState.Starting);

    startClient(context, 0);
}

/* ------------------------------------------------------------------ */
/*  Deactivation                                                      */
/* ------------------------------------------------------------------ */

export function deactivate(): Thenable<void> | undefined {
    return client?.stop();
}

/* ------------------------------------------------------------------ */
/*  Client lifecycle                                                  */
/* ------------------------------------------------------------------ */

function startClient(
    context: vscode.ExtensionContext,
    attempt: number,
): void {
    const config = vscode.workspace.getConfiguration("pyxle.langserver");
    const command = config.get<string>("command", "pyxle-langserver");
    const args = config.get<string[]>("args", ["--stdio"]);

    const serverOptions: ServerOptions = {
        run: { command, args },
        debug: { command, args },
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [{ scheme: "file", language: LANGUAGE_ID }],
        synchronize: {
            fileEvents: vscode.workspace.createFileSystemWatcher("**/*.pyx"),
        },
        outputChannel,
    };

    client = new LanguageClient(
        "pyxle",
        "Pyxle Language Server",
        serverOptions,
        clientOptions,
    );

    const startPromise = client.start();

    client.onDidChangeState((event) => {
        if (event.newState === 2 /* Running */) {
            updateStatus(statusBar, StatusState.Running);
        }
    });

    const onReady = (): void => {
        updateStatus(statusBar, StatusState.Running);
    };

    const onError = (error: unknown): void => {
        const message =
            error instanceof Error ? error.message : String(error);
        outputChannel.appendLine(
            `Language server failed to start: ${message}`,
        );

        if (attempt < MAX_RETRIES) {
            const next = attempt + 1;
            updateStatus(statusBar, StatusState.Retrying, next, MAX_RETRIES);
            outputChannel.appendLine(
                `Retrying in ${RETRY_DELAY_MS}ms (attempt ${next}/${MAX_RETRIES})...`,
            );
            setTimeout(() => startClient(context, next), RETRY_DELAY_MS);
        } else {
            updateStatus(statusBar, StatusState.Failed);
            vscode.window
                .showErrorMessage(
                    `Pyxle Language Server failed after ${MAX_RETRIES} attempts. Is pyxle-langserver installed?`,
                    "Show Output",
                )
                .then((choice) => {
                    if (choice === "Show Output") {
                        outputChannel.show();
                    }
                });
        }
    };

    startPromise.then(onReady, onError);

    context.subscriptions.push({ dispose: () => { client?.stop(); } });
}
