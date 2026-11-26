<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { machineHttpBaseUrlFromWsUrl, getBackendHttpBase } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SettingRow from '$lib/components/ui/SettingRow.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';

	// The StudSphere connection (studsphere_sync.py on the backend): sorted
	// pieces are booked into the owner's StudSphere live, bin by bin.
	type Config = {
		enabled: boolean;
		url: string;
		has_api_key: boolean;
		api_key_hint: string | null;
		condition: 'used' | 'new';
	};
	type Status = {
		running: boolean;
		set_up: boolean;
		location: string | null;
		pending: number;
		last_sync_at: number | null;
		last_error: string | null;
		unmapped: { part: string; color: string; missing: 'part' | 'color' | 'both' }[];
		hinted: { part: string; as: string }[];
	};

	const STATUS_POLL_MS = 3000;
	const machine = getMachineContext();

	let config = $state<Config | null>(null);
	let status = $state<Status | null>(null);
	let loadError = $state<string | null>(null);

	let enabledDraft = $state(false);
	let urlDraft = $state('');
	let keyDraft = $state('');
	let conditionDraft = $state<'used' | 'new'>('used');

	let saving = $state(false);
	let saveError = $state<string | null>(null);
	let testing = $state(false);
	let testResult = $state<string | null>(null);
	let testError = $state<string | null>(null);
	let resyncing = $state(false);
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	function httpBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	function apply(body: { config: Config; status: Status }, resetDrafts: boolean) {
		config = body.config;
		status = body.status;
		if (resetDrafts) {
			enabledDraft = body.config.enabled;
			urlDraft = body.config.url;
			conditionDraft = body.config.condition;
			keyDraft = '';
		}
	}

	async function load(resetDrafts: boolean) {
		try {
			const res = await fetch(`${httpBase()}/api/studsphere`);
			if (!res.ok) throw new Error(await res.text());
			apply(await res.json(), resetDrafts);
			loadError = null;
		} catch (e: any) {
			if (!config) loadError = e.message ?? 'Failed to load the StudSphere settings';
		}
	}

	const dirty = $derived(
		!!config &&
			(enabledDraft !== config.enabled ||
				urlDraft.trim() !== config.url ||
				conditionDraft !== config.condition ||
				keyDraft.trim() !== '')
	);

	async function post(path: string, body?: unknown) {
		const res = await fetch(`${httpBase()}${path}`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		const data = await res.json().catch(() => null);
		if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`);
		return data;
	}

	async function save() {
		saving = true;
		saveError = null;
		try {
			apply(
				await post('/api/studsphere/config', {
					enabled: enabledDraft,
					url: urlDraft,
					api_key: keyDraft,
					condition: conditionDraft
				}),
				true
			);
		} catch (e: any) {
			saveError = e.message ?? 'Failed to save';
		} finally {
			saving = false;
		}
	}

	async function test() {
		testing = true;
		testResult = null;
		testError = null;
		try {
			const data = await post('/api/studsphere/test');
			testResult = data.machine_id
				? `Key "${data.key_name}" works, set up as ${data.location ?? 'a location that no longer exists'}.`
				: `Key "${data.key_name}" works and isn't bound to a sorter yet.`;
		} catch (e: any) {
			testError = e.message ?? 'The test failed';
		} finally {
			testing = false;
		}
	}

	async function resync() {
		resyncing = true;
		saveError = null;
		try {
			apply(await post('/api/studsphere/resync'), false);
		} catch (e: any) {
			saveError = e.message ?? 'Resync failed';
		} finally {
			resyncing = false;
		}
	}

	function timeAgo(ts: number | null): string {
		if (!ts) return 'Never';
		const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
		if (s < 60) return `${s} s ago`;
		if (s < 3600) return `${Math.round(s / 60)} min ago`;
		return new Date(ts * 1000).toLocaleString();
	}

	const connection = $derived.by((): { value: string; tone?: 'success' | 'warning' | 'danger' } => {
		if (!config?.enabled) return { value: 'Off' };
		if (status?.last_error) return { value: 'Error', tone: 'danger' };
		if (status?.set_up) return { value: 'Connected', tone: 'success' };
		return { value: 'Connecting', tone: 'warning' };
	});

	onMount(() => {
		void load(true);
		pollHandle = setInterval(() => void load(false), STATUS_POLL_MS);
	});
	onDestroy(() => {
		if (pollHandle) clearInterval(pollHandle);
	});
</script>

<svelte:head><title>Sorter - StudSphere</title></svelte:head>

<PageHeader
	title="StudSphere"
	description="Book sorted parts into your StudSphere live: a location for this sorter, one per bin, and one per emptied bin with a receipt."
/>

{#if loadError}
	<Alert tone="danger">{loadError}</Alert>
{:else if !config || !status}
	<div class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> Loading</div>
{:else}
	<div class="flex flex-col gap-4">
		<Panel title="Status">
			{#snippet actions()}
				<Button size="sm" loading={resyncing} disabled={!config?.enabled} onclick={() => void resync()}>
					Resync all bins
				</Button>
			{/snippet}
			<div class="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
				<div class="bg-surface"><Stat label="Connection" value={connection.value} tone={connection.tone} /></div>
				<div class="bg-surface">
					<Stat label="Waiting to send" value={status.pending} tone={status.pending > 500 ? 'warning' : undefined} />
				</div>
				<div class="bg-surface"><Stat label="Last sent" value={timeAgo(status.last_sync_at)} /></div>
				<div class="bg-surface">
					<Stat
						label="Unknown to StudSphere"
						value={status.unmapped.length}
						tone={status.unmapped.length ? 'warning' : undefined}
					/>
				</div>
			</div>
			{#if status.location}
				<div class="px-(--pad-panel) py-2 text-sm text-ink-muted">
					Location in StudSphere: <span class="text-ink">{status.location}</span>
				</div>
			{/if}
			{#if status.last_error}
				<div class="px-(--pad-panel) py-2"><Alert tone="danger">{status.last_error}</Alert></div>
			{/if}
			{#if status.hinted.length}
				<div class="px-(--pad-panel) py-2 text-sm text-ink-muted">
					Booked under the number our parts catalog gives for them, because StudSphere had no match for the BrickLink
					number. Worth a look, they can be a similar part:
					<ul class="mt-1 font-mono text-ink">
						{#each status.hinted as h (h.part)}
							<li>{h.part} <span class="font-sans text-ink-muted">booked as</span> {h.as}</li>
						{/each}
					</ul>
				</div>
			{/if}
			{#if status.unmapped.length}
				<div class="px-(--pad-panel) py-2 text-sm text-ink-muted">
					Not booked, because StudSphere doesn't know the part or color under its BrickLink number.
					"Resync all bins" books them once it does. StudSphere asks Rebrickable for only a few unknown numbers per
					request, so after a large first sync it can take a few resyncs until none are left. Assemblies (c01, c02 ...)
					are never matched through a hint, so they stay here until StudSphere knows them.
					<ul class="mt-1 font-mono text-ink">
						{#each status.unmapped as u (`${u.part}|${u.color}`)}
							<li>
								{u.part} / color {u.color}
								<span class="font-sans text-ink-muted">
									- unknown {u.missing === 'both' ? 'part and color' : u.missing}
								</span>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
		</Panel>

		<Panel
			title="Connection"
			description="Make a key for this sorter in StudSphere under Settings > Sorting machines; it shows the address to enter here too. Turning this on, or a new address or key, sends every bin's current contents first."
		>
			<div class="divide-y divide-line">
				<SettingRow label="Book into StudSphere">
					<Switch bind:checked={enabledDraft} label="Book into StudSphere" />
				</SettingRow>
				<SettingRow label="StudSphere address" help="Where StudSphere's index.php is." for="ss-url">
					<Input id="ss-url" bind:value={urlDraft} placeholder="https://studsphere.example/" class="w-80" />
				</SettingRow>
				<SettingRow
					label="API key"
					help={config.has_api_key
						? `Saved (${config.api_key_hint ?? 'hidden'}). Leave blank to keep it.`
						: 'Shown once in StudSphere when the key is made.'}
					for="ss-key"
				>
					<Input
						id="ss-key"
						type="password"
						autocomplete="off"
						bind:value={keyDraft}
						placeholder={config.has_api_key ? '••••••••' : 'ssk_…'}
						class="w-80 font-mono"
					/>
				</SettingRow>
				<SettingRow label="Condition" help="How sorted parts are booked in StudSphere." for="ss-condition">
					<Select
						id="ss-condition"
						bind:value={conditionDraft}
						options={[
							{ value: 'used', label: 'Used' },
							{ value: 'new', label: 'New' }
						]}
						class="w-32"
					/>
				</SettingRow>
			</div>
			{#if saveError}
				<div class="px-(--pad-panel) py-2"><Alert tone="danger">{saveError}</Alert></div>
			{/if}
			{#if testError}
				<div class="px-(--pad-panel) py-2"><Alert tone="danger">{testError}</Alert></div>
			{:else if testResult}
				<div class="px-(--pad-panel) py-2 text-sm text-ink-muted">{testResult}</div>
			{/if}
			<div class="flex items-center justify-end gap-3 px-(--pad-panel) py-(--pad-row)">
				<Button
					loading={testing}
					disabled={!config.url || !config.has_api_key || dirty}
					onclick={() => void test()}
				>
					Test connection
				</Button>
				<Button variant="primary" loading={saving} disabled={!dirty} onclick={() => void save()}>Save</Button>
			</div>
		</Panel>
	</div>
{/if}
