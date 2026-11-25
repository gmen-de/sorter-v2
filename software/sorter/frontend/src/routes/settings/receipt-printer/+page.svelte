<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { machineHttpBaseUrlFromWsUrl, getBackendHttpBase } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SettingRow from '$lib/components/ui/SettingRow.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Textarea from '$lib/components/ui/Textarea.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';

	// The receipt printer (receipt_printer.py on the backend): an ESC/POS
	// printer such as the Epson TM-T88IV on a serial port, usually an FTDI
	// USB-RS232 adapter.
	type Settings = {
		enabled: boolean;
		port: string;
		baudrate: number;
		bytesize: number;
		parity: 'none' | 'even' | 'odd';
		stopbits: number;
		flow_control: 'rtscts' | 'xonxoff' | 'none';
		columns: number;
		codepage: string;
		cut: 'partial' | 'full' | 'none';
		feed_lines: number;
		header: string;
		footer: string;
	};
	type Port = {
		path: string;
		device: string;
		description: string | null;
		manufacturer: string | null;
		serial_number: string | null;
		vid_pid: string | null;
		is_ftdi: boolean;
		is_control_board: boolean;
	};
	type PrinterInfo = {
		settings: Settings;
		ports: Port[];
		options: {
			baudrates: number[];
			codepages: { value: string; label: string }[];
		};
	};
	type Status = {
		reachable: boolean;
		reason?: 'port' | 'no_reply' | 'handshake';
		error?: string;
		online?: boolean;
		cover_open?: boolean;
		feed_button?: boolean;
		paper_out?: boolean;
		paper_near_end?: boolean;
		cutter_error?: boolean;
		unrecoverable_error?: boolean;
		auto_recoverable_error?: boolean;
	};

	const STATUS_POLL_MS = 5000;
	const machine = getMachineContext();

	let info = $state<PrinterInfo | null>(null);
	let draft = $state<Settings | null>(null);
	let loadError = $state<string | null>(null);
	let saving = $state(false);
	let saveError = $state<string | null>(null);
	let saved = $state('');

	let status = $state<Status | null>(null);
	let statusLoading = $state(false);
	let statusHandle: ReturnType<typeof setInterval> | null = null;

	let testing = $state(false);
	let printing = $state(false);
	let jobError = $state<string | null>(null);
	let jobDone = $state('');
	let freeText = $state('');

	function httpBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	async function load() {
		loadError = null;
		try {
			const res = await fetch(`${httpBase()}/api/receipt-printer`);
			if (!res.ok) throw new Error(await res.text());
			info = await res.json();
			if (info) draft = { ...info.settings };
		} catch (e: any) {
			loadError = e.message ?? 'Failed to load the printer settings';
		}
	}

	async function loadStatus() {
		if (statusLoading || !info?.settings.port) return;
		statusLoading = true;
		try {
			const res = await fetch(`${httpBase()}/api/receipt-printer/status`);
			if (res.ok) status = await res.json();
		} catch {
			// The next poll tries again.
		} finally {
			statusLoading = false;
		}
	}

	const dirty = $derived(
		!!draft && !!info && JSON.stringify(draft) !== JSON.stringify(info.settings)
	);

	async function save() {
		if (!draft) return;
		saving = true;
		saveError = null;
		saved = '';
		try {
			const res = await fetch(`${httpBase()}/api/receipt-printer/settings`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					...draft,
					columns: Number(draft.columns),
					feed_lines: Number(draft.feed_lines)
				})
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const body = await res.json();
			if (info) info.settings = body.settings;
			draft = { ...body.settings };
			saved = 'Saved.';
			status = null;
			void loadStatus();
		} catch (e: any) {
			saveError = e.message ?? 'Failed to save the printer settings';
		} finally {
			saving = false;
		}
	}

	async function runJob(path: string, body: unknown, done: string) {
		jobError = null;
		jobDone = '';
		const res = await fetch(`${httpBase()}${path}`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		if (!res.ok) {
			const detail = await res.json().catch(() => null);
			throw new Error(detail?.detail ?? `HTTP ${res.status}`);
		}
		jobDone = done;
		void loadStatus();
	}

	async function testPrint() {
		testing = true;
		try {
			await runJob('/api/receipt-printer/test', undefined, 'Test page sent.');
		} catch (e: any) {
			jobError = e.message ?? 'The test print failed';
		} finally {
			testing = false;
		}
	}

	async function printFreeText() {
		printing = true;
		try {
			await runJob('/api/receipt-printer/print', { text: freeText }, 'Text sent.');
		} catch (e: any) {
			jobError = e.message ?? 'Printing failed';
		} finally {
			printing = false;
		}
	}

	function portLabel(p: Port): string {
		const name = p.path.startsWith('/dev/serial/by-id/') ? p.device : p.path;
		const what = [p.manufacturer, p.description].filter(Boolean).join(' ');
		return what ? `${name} - ${what}` : name;
	}

	const portOptions = $derived.by(() => {
		const ports = info?.ports ?? [];
		const options = ports.map((p) => ({
			value: p.path,
			label: portLabel(p),
			hint: p.is_control_board
				? 'Control board'
				: p.is_ftdi
					? `FTDI${p.serial_number ? `, serial ${p.serial_number}` : ''}`
					: (p.vid_pid ?? undefined),
			disabled: p.is_control_board
		}));
		const current = draft?.port;
		if (current && !ports.some((p) => p.path === current)) {
			options.unshift({ value: current, label: `${current} (not plugged in)`, hint: undefined, disabled: false });
		}
		return [{ value: '', label: 'None', hint: undefined, disabled: false }, ...options];
	});

	const connection = $derived.by((): { value: string; tone?: 'success' | 'warning' | 'danger'; hint?: string } => {
		if (!info?.settings.port) return { value: 'No port' };
		if (!status) return { value: '--' };
		if (!status.reachable) {
			if (status.reason === 'port') return { value: 'Port error', tone: 'danger' };
			if (status.reason === 'handshake') return { value: 'Busy', tone: 'warning', hint: 'Handshake line not ready' };
			return { value: 'No reply', tone: 'danger', hint: 'Off, wrong baud rate or cable' };
		}
		return status.online ? { value: 'Online', tone: 'success' } : { value: 'Offline', tone: 'warning' };
	});

	const paper = $derived.by((): { value: string; tone?: 'success' | 'warning' | 'danger' } => {
		if (!status?.reachable) return { value: '--' };
		if (status.paper_out) return { value: 'Out', tone: 'danger' };
		if (status.paper_near_end) return { value: 'Near end', tone: 'warning' };
		return { value: 'OK', tone: 'success' };
	});

	const errors = $derived.by((): { value: string; tone?: 'success' | 'warning' | 'danger' } => {
		if (!status?.reachable) return { value: '--' };
		if (status.unrecoverable_error) return { value: 'Fault', tone: 'danger' };
		if (status.cutter_error) return { value: 'Cutter', tone: 'danger' };
		if (status.auto_recoverable_error) return { value: 'Head hot', tone: 'warning' };
		return { value: 'None', tone: 'success' };
	});

	onMount(() => {
		void load().then(loadStatus);
		statusHandle = setInterval(() => void loadStatus(), STATUS_POLL_MS);
	});
	onDestroy(() => {
		if (statusHandle) clearInterval(statusHandle);
	});
</script>

<svelte:head><title>Sorter - Receipt printer</title></svelte:head>

<PageHeader
	title="Receipt printer"
	description="An ESC/POS receipt printer such as the Epson TM-T88IV on a serial port, usually through an FTDI USB-RS232 adapter."
/>

{#if loadError}
	<Alert tone="danger">{loadError}</Alert>
{:else if !info || !draft}
	<div class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> Loading</div>
{:else}
	<div class="flex flex-col gap-4">
		<Panel title="Status" description="Asked from the printer every 5 seconds, with the saved settings.">
			{#snippet actions()}
				<Button size="sm" loading={statusLoading} disabled={!info?.settings.port} onclick={() => void loadStatus()}>
					Refresh
				</Button>
				<Button
					size="sm"
					variant="primary"
					loading={testing}
					disabled={!info?.settings.port || dirty}
					onclick={() => void testPrint()}
				>
					Test print
				</Button>
			{/snippet}
			<div class="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
				<div class="bg-surface">
					<Stat label="Connection" value={connection.value} tone={connection.tone} hint={connection.hint} />
				</div>
				<div class="bg-surface"><Stat label="Paper" value={paper.value} tone={paper.tone} /></div>
				<div class="bg-surface">
					<Stat
						label="Cover"
						value={status?.reachable ? (status.cover_open ? 'Open' : 'Closed') : '--'}
						tone={status?.reachable ? (status.cover_open ? 'danger' : 'success') : undefined}
					/>
				</div>
				<div class="bg-surface"><Stat label="Errors" value={errors.value} tone={errors.tone} /></div>
			</div>
			{#if status?.error}
				<div class="px-(--pad-panel) py-2"><Alert tone="danger">{status.error}</Alert></div>
			{/if}
			{#if dirty}
				<div class="px-(--pad-panel) py-2 text-sm text-ink-muted">Save the settings before a test print.</div>
			{/if}
			{#if jobError}
				<div class="px-(--pad-panel) py-2"><Alert tone="danger">{jobError}</Alert></div>
			{:else if jobDone}
				<div class="px-(--pad-panel) py-2 text-sm text-ink-muted">{jobDone}</div>
			{/if}
		</Panel>

		<Panel
			title="Connection"
			description="Has to match the printer's DIP switches. Hold FEED while switching the printer on: its self-test page lists them."
		>
			{#snippet actions()}
				<Button size="sm" onclick={() => void load()}>Find ports</Button>
			{/snippet}
			<div class="divide-y divide-line">
				<SettingRow
					label="Printer on"
					help="Lets the machine print on its own. Test prints and free text work while it's off."
				>
					<Switch bind:checked={draft.enabled} label="Printer on" />
				</SettingRow>
				<SettingRow
					label="Port"
					help="Saved by the adapter's serial number (/dev/serial/by-id), so it stays the same adapter after a reboot. Control boards can't be picked."
					for="rp-port"
				>
					<Select id="rp-port" bind:value={draft.port} options={portOptions} class="w-80" />
				</SettingRow>
				<SettingRow label="Baud rate" for="rp-baud">
					<Select
						id="rp-baud"
						value={String(draft.baudrate)}
						onchange={(v) => draft && (draft.baudrate = Number(v))}
						options={info.options.baudrates.map((b) => ({ value: String(b), label: String(b) }))}
						class="w-32"
					/>
				</SettingRow>
				<SettingRow label="Data bits" for="rp-bytesize">
					<Select
						id="rp-bytesize"
						value={String(draft.bytesize)}
						onchange={(v) => draft && (draft.bytesize = Number(v))}
						options={[
							{ value: '8', label: '8' },
							{ value: '7', label: '7' }
						]}
						class="w-32"
					/>
				</SettingRow>
				<SettingRow label="Parity" for="rp-parity">
					<Select
						id="rp-parity"
						bind:value={draft.parity}
						options={[
							{ value: 'none', label: 'None' },
							{ value: 'even', label: 'Even' },
							{ value: 'odd', label: 'Odd' }
						]}
						class="w-32"
					/>
				</SettingRow>
				<SettingRow label="Stop bits" for="rp-stopbits">
					<Select
						id="rp-stopbits"
						value={String(draft.stopbits)}
						onchange={(v) => draft && (draft.stopbits = Number(v))}
						options={[
							{ value: '1', label: '1' },
							{ value: '2', label: '2' }
						]}
						class="w-32"
					/>
				</SettingRow>
				<SettingRow
					label="Flow control"
					help="RTS/CTS: the printer signals busy on a handshake line, which a null-modem cable brings to the adapter's CTS. XON/XOFF works over three wires but only for plain text."
					for="rp-flow"
				>
					<Select
						id="rp-flow"
						bind:value={draft.flow_control}
						options={[
							{ value: 'rtscts', label: 'RTS/CTS' },
							{ value: 'xonxoff', label: 'XON/XOFF' },
							{ value: 'none', label: 'None' }
						]}
						class="w-32"
					/>
				</SettingRow>
			</div>
		</Panel>

		<Panel title="Paper and layout">
			<div class="divide-y divide-line">
				<SettingRow
					label="Characters per line"
					help="42 for 80 mm paper, 30 for 58 mm (font A). The test page's ruler shows whether it fits."
					for="rp-columns"
				>
					<Input id="rp-columns" type="number" min="16" max="80" step="1" bind:value={draft.columns} class="w-28" />
				</SettingRow>
				<SettingRow
					label="Code page"
					help="The character table for accents and umlauts. PC858 has the German umlauts and the euro sign."
					for="rp-codepage"
				>
					<Select id="rp-codepage" bind:value={draft.codepage} options={info.options.codepages} class="w-80" />
				</SettingRow>
				<SettingRow label="Cut" for="rp-cut">
					<Select
						id="rp-cut"
						bind:value={draft.cut}
						options={[
							{ value: 'partial', label: 'Partial cut' },
							{ value: 'full', label: 'Full cut' },
							{ value: 'none', label: 'No cut' }
						]}
						class="w-32"
					/>
				</SettingRow>
				<SettingRow
					label="Feed before cut"
					help="Blank lines after the last line, so it clears the cutter."
					for="rp-feed"
				>
					<Input id="rp-feed" type="number" min="0" max="20" step="1" bind:value={draft.feed_lines} class="w-28" />
				</SettingRow>
				<SettingRow label="Header" help="Printed centred and bold at the top of every receipt." for="rp-header">
					<Textarea id="rp-header" rows={2} bind:value={draft.header} class="w-80 font-mono" />
				</SettingRow>
				<SettingRow label="Footer" help="Printed centred at the bottom of every receipt." for="rp-footer">
					<Textarea id="rp-footer" rows={2} bind:value={draft.footer} class="w-80 font-mono" />
				</SettingRow>
			</div>
			{#if saveError}
				<div class="px-(--pad-panel) py-2"><Alert tone="danger">{saveError}</Alert></div>
			{/if}
			<div class="flex items-center justify-end gap-3 px-(--pad-panel) py-(--pad-row)">
				{#if saved && !dirty}<span class="text-sm text-ink-muted">{saved}</span>{/if}
				<Button
					variant="secondary"
					disabled={!dirty}
					onclick={() => info && (draft = { ...info.settings })}
				>
					Discard
				</Button>
				<Button variant="primary" loading={saving} disabled={!dirty} onclick={() => void save()}>Save</Button>
			</div>
		</Panel>

		<Panel title="Print text" description="Prints this text with the header and footer, then cuts.">
			<div class="flex flex-col gap-3 px-(--pad-panel) pb-(--pad-panel)">
				<Textarea rows={4} bind:value={freeText} class="font-mono" aria-label="Text to print" />
				<div class="flex justify-end">
					<Button
						loading={printing}
						disabled={!freeText.trim() || !info.settings.port || dirty}
						onclick={() => void printFreeText()}
					>
						Print
					</Button>
				</div>
			</div>
		</Panel>
	</div>
{/if}
