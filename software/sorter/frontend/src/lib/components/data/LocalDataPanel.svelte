<!--
	Settings > Data: what is stored on this machine until the Hives that should
	have it do, per kind, with the disk it is kept on and the way to delete the
	samples.
-->
<script lang="ts">
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { confirmDialog } from '$lib/confirm.svelte';
	import { getMachineContext } from '$lib/machines/context';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import { getSharingStats, type SharingStats } from '$lib/sharing/api';
	import { formatBytes, formatCount } from '$lib/sharing/format';

	const machine = getMachineContext();

	const POLL_MS = 30_000;

	const base = $derived(machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase());

	let stats = $state<SharingStats | null>(null);
	let loadError = $state<string | null>(null);
	let deleting = $state(false);
	let deleteError = $state<string | null>(null);
	let deleted = $state<string | null>(null);

	const samples = $derived(stats?.local.find((kind) => kind.key === 'samples')?.count ?? 0);
	const diskLow = $derived(
		stats !== null &&
			stats.disk.free_bytes !== null &&
			stats.disk.free_bytes < stats.disk.min_free_bytes
	);

	function message(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function load(from: string) {
		try {
			const next = await getSharingStats(from, 24);
			if (from !== base) return;
			stats = next;
			loadError = null;
		} catch (e) {
			if (from === base)
				loadError = $_('components.data.localDataPanel.loadErrorPrefix', {
					values: { message: message(e) }
				});
		}
	}

	$effect(() => {
		const from = base;
		void load(from);
		const timer = setInterval(() => void load(from), POLL_MS);
		return () => clearInterval(timer);
	});

	async function deleteSamples() {
		const sure = await confirmDialog({
			title: $_('components.data.localDataPanel.confirmTitle'),
			message: $_('components.data.localDataPanel.confirmMessage'),
			action: $_('components.data.localDataPanel.confirmAction'),
			danger: true
		});
		if (!sure) return;
		deleting = true;
		deleteError = null;
		deleted = null;
		try {
			const res = await fetch(`${base}/api/samples/storage`, { method: 'DELETE' });
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json().catch(() => null);
			deleted = data?.message ?? $_('components.data.localDataPanel.deletedFallback');
			await load(base);
		} catch (e) {
			deleteError = message(e) || $_('components.data.localDataPanel.deleteFailed');
		} finally {
			deleting = false;
		}
	}
</script>

{#snippet deleteButton()}
	<Button variant="danger" size="sm" icon={Trash2} loading={deleting} onclick={deleteSamples}>
		{$_('components.data.localDataPanel.deleteButtonLabel')}
	</Button>
{/snippet}

<Panel
	title={$_('components.data.localDataPanel.panelTitle')}
	description={$_('components.data.localDataPanel.panelDescription')}
	footer={samples > 0 ? deleteButton : undefined}
	flush
>
	{#if loadError && !stats}
		<div class="px-(--pad-panel) pb-4">
			<Alert tone="danger">{loadError}</Alert>
		</div>
	{:else if !stats}
		<p class="flex items-center gap-2 px-(--pad-panel) pb-4 text-sm text-ink-muted">
			<Spinner size={14} /> {$_('components.data.localDataPanel.loadingLabel')}
		</p>
	{:else}
		{#if stats.local.length === 0}
			<p class="px-(--pad-panel) pb-4 text-sm text-ink-muted">
				{$_('components.data.localDataPanel.nothingStored')}
			</p>
		{:else}
			<div class="overflow-x-auto">
				<table class="data-table">
					<thead>
						<tr>
							<th>{$_('components.data.localDataPanel.tableHeaders.kind')}</th>
							<th class="num">{$_('components.data.localDataPanel.tableHeaders.stored')}</th>
							<th class="num">{$_('components.data.localDataPanel.tableHeaders.size')}</th>
							<th class="num"
								>{$_('components.data.localDataPanel.tableHeaders.waitingForHive')}</th
							>
						</tr>
					</thead>
					<tbody>
						{#each stats.local as kind (kind.key)}
							<tr>
								<td>{kind.label}</td>
								<td class="num">{formatCount(kind.count)}</td>
								<td class="num whitespace-nowrap">
									{formatBytes(kind.bytes)}{#if kind.cap_bytes}{' '}<span class="text-ink-muted"
											>{$_('components.data.localDataPanel.capLabel', {
												values: { cap: formatBytes(kind.cap_bytes) }
											})}</span
										>{/if}
								</td>
								<td class="num">{formatCount(kind.waiting)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}

		{#if stats.disk.free_bytes !== null}
			<p
				class="border-t border-line px-(--pad-panel) py-3 text-sm {diskLow
					? 'text-warning-ink'
					: 'text-ink-muted'}"
			>
				<span class="num font-medium {diskLow ? '' : 'text-ink'}">
					{formatBytes(stats.disk.free_bytes)}
				</span>
				{$_(
					diskLow
						? 'components.data.localDataPanel.diskStatus.low'
						: 'components.data.localDataPanel.diskStatus.ok',
					{ values: { min: formatBytes(stats.disk.min_free_bytes) } }
				)}
			</p>
		{/if}
	{/if}

	{#if deleted}
		<div class="border-t border-line px-(--pad-panel) py-3">
			<Alert tone="success">{deleted}</Alert>
		</div>
	{/if}
	{#if deleteError}
		<div class="border-t border-line px-(--pad-panel) py-3">
			<Alert tone="danger">{deleteError}</Alert>
		</div>
	{/if}
</Panel>
