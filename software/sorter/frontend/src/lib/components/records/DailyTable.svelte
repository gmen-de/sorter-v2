<script lang="ts">
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Download from '@lucide/svelte/icons/download';
	import { _ } from 'svelte-i18n';
	import Button from '$lib/components/ui/Button.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import type { LifetimeDay } from './RecordsStats.svelte';

	let {
		daily,
		exportUrl
	}: {
		daily: LifetimeDay[];
		exportUrl: string;
	} = $props();

	// Two-week blocks; rows arrive newest-first from the backend, so block 0 is
	// the current fortnight.
	const BLOCK_SIZE = 14;
	let block = $state(0);

	const blockCount = $derived(Math.max(1, Math.ceil(daily.length / BLOCK_SIZE)));
	const rows = $derived(daily.slice(block * BLOCK_SIZE, (block + 1) * BLOCK_SIZE));

	$effect(() => {
		if (block >= blockCount) block = Math.max(0, blockCount - 1);
	});

	function formatDayLabel(day: string): string {
		const d = new Date(`${day}T00:00:00`);
		if (Number.isNaN(d.getTime())) return day;
		return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}

	function formatDuration(seconds: number | null | undefined): string {
		if (!seconds || seconds <= 0) return '0h 0m';
		const total_min = Math.floor(seconds / 60);
		const days = Math.floor(total_min / 1440);
		const hours = Math.floor((total_min % 1440) / 60);
		const mins = total_min % 60;
		if (days > 0) return `${days}d ${hours}h`;
		return `${hours}h ${mins}m`;
	}

	function formatPpm(ppm: number): string {
		if (!ppm || ppm <= 0) return '—';
		return ppm.toLocaleString(undefined, { maximumFractionDigits: 1 });
	}

	const rangeLabel = $derived.by(() => {
		if (rows.length === 0) return '';
		const newest = formatDayLabel(rows[0].day);
		const oldest = formatDayLabel(rows[rows.length - 1].day);
		return rows.length === 1 ? newest : `${oldest} – ${newest}`;
	});
</script>

{#snippet pager()}
	<div class="flex w-full flex-wrap items-center justify-end gap-2">
		<span class="num mr-auto text-sm text-ink-muted">{block + 1} of {blockCount}</span>
		<Button
			size="sm"
			icon={ChevronLeft}
			disabled={block >= blockCount - 1}
			onclick={() => (block = Math.min(blockCount - 1, block + 1))}
		>
			{$_('components.records.dailyTable.olderTwoWeeks')}
		</Button>
		<Button size="sm" disabled={block <= 0} onclick={() => (block = Math.max(0, block - 1))}>
			{$_('components.records.dailyTable.newerTwoWeeks')}
			<ChevronRight size={14} />
		</Button>
	</div>
{/snippet}

{#if daily.length > 0}
	<Panel
		title={$_('components.records.dailyTable.panelTitle')}
		description={rangeLabel}
		flush
		footer={blockCount > 1 ? pager : undefined}
	>
		{#snippet actions()}
			<Button size="sm" icon={Download} href={exportUrl} download>
				{$_('components.records.dailyTable.exportCsvButton')}
			</Button>
		{/snippet}
		<div class="overflow-x-auto">
			<table class="data-table">
				<thead>
					<tr>
						<th>{$_('components.records.dailyTable.dayHeader')}</th>
						<th class="num">{$_('components.records.dailyTable.poweredHeader')}</th>
						<th class="num">{$_('components.records.dailyTable.sortedHeader')}</th>
						<th class="num">{$_('components.records.dailyTable.piecesHeader')}</th>
						<th class="num">{$_('components.records.dailyTable.classifiedHeader')}</th>
						<th class="num">{$_('components.records.dailyTable.ppmHeader')}</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as d (d.day)}
						<tr>
							<td>{formatDayLabel(d.day)}</td>
							<td class="num text-ink-muted">{formatDuration(d.seconds_powered)}</td>
							<td class="num">{formatDuration(d.seconds_sorted)}</td>
							<td class="num">{d.pieces_distributed.toLocaleString()}</td>
							<td class="num text-ink-muted">{d.pieces_classified.toLocaleString()}</td>
							<td class="num">
								{formatPpm(d.seconds_sorted > 0 ? (d.pieces_distributed * 60) / d.seconds_sorted : 0)}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Panel>
{/if}
