<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Download from '@lucide/svelte/icons/download';
	import { _ } from 'svelte-i18n';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import { formatCategoryName, formatLastSeen } from './pieces';
	import type { SnapshotDetail, SnapshotLayer, SnapshotSummary } from './types';

	let { open = $bindable(false), baseUrl }: { open?: boolean; baseUrl: string } = $props();

	let snapshots = $state<SnapshotSummary[]>([]);
	let snapshotsLoading = $state(false);
	let snapshotsError = $state<string | null>(null);
	let detail = $state<SnapshotDetail | null>(null);
	let detailLoading = $state(false);
	let lastOpen = false;

	$effect(() => {
		if (open && !lastOpen) {
			detail = null;
			void loadSnapshots();
		}
		lastOpen = open;
	});

	async function loadSnapshots() {
		snapshotsLoading = true;
		snapshotsError = null;
		try {
			const res = await fetch(`${baseUrl}/api/bins/snapshots`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const data = await res.json();
			snapshots = data.snapshots ?? [];
		} catch (e) {
			snapshotsError = $_('components.bins.snapshotsModal.loadSnapshotsError', { values: { error: String(e) } });
		} finally {
			snapshotsLoading = false;
		}
	}

	async function openDetail(id: string) {
		detailLoading = true;
		snapshotsError = null;
		try {
			const res = await fetch(`${baseUrl}/api/bins/snapshots/${encodeURIComponent(id)}`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			detail = await res.json();
		} catch (e) {
			snapshotsError = $_('components.bins.snapshotsModal.loadSnapshotError', { values: { error: String(e) } });
		} finally {
			detailLoading = false;
		}
	}

	function csvUrl(id: string): string {
		return `${baseUrl}/api/bins/snapshots/${encodeURIComponent(id)}/export.csv`;
	}

	function binLabel(layer: SnapshotLayer): string {
		return $_('components.bins.snapshotsModal.binLabel', {
			values: { layer: layer.layer_index + 1, section: layer.section_index + 1, bin: layer.bin_index + 1 }
		});
	}
</script>

<Modal
	bind:open
	title={detail
		? $_('components.bins.snapshotsModal.snapshotDetailsTitle')
		: $_('components.bins.snapshotsModal.binSnapshotsTitle')}
	size="lg"
>
	{#if snapshotsError}<Alert tone="danger" class="mb-4">{snapshotsError}</Alert>{/if}
	{#if detail}
		<div class="flex flex-col gap-5">
			<div class="flex items-center justify-between gap-3">
				<Button size="sm" icon={ArrowLeft} onclick={() => (detail = null)}
					>{$_('components.bins.snapshotsModal.allSnapshotsButton')}</Button
				>
				<Button size="sm" icon={Download} href={csvUrl(detail.id)} download>
					{$_('components.bins.snapshotsModal.exportCsvButton')}
				</Button>
			</div>
			<dl class="grid grid-cols-1 gap-4 sm:grid-cols-4">
				<div>
					<dt class="label">{$_('components.bins.snapshotsModal.statusLabel')}</dt>
					<dd class="mt-1 text-base font-medium text-ink capitalize">{detail.status}</dd>
				</div>
				<div>
					<dt class="label">{$_('components.bins.snapshotsModal.startedLabel')}</dt>
					<dd class="mt-1 text-base font-medium text-ink">{formatLastSeen(detail.created_at)}</dd>
				</div>
				<div>
					<dt class="label">{$_('components.bins.snapshotsModal.closedLabel')}</dt>
					<dd class="mt-1 text-base font-medium text-ink">{formatLastSeen(detail.closed_at)}</dd>
				</div>
				<div>
					<dt class="label">{$_('components.bins.snapshotsModal.piecesLabel')}</dt>
					<dd class="num mt-1 text-base font-medium text-ink">{detail.piece_count}</dd>
				</div>
			</dl>
			{#each detail.layers as layer (layer.id)}
				<section class="flex flex-col gap-2">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<h3 class="text-base font-semibold text-ink">{binLabel(layer)}</h3>
						<div class="flex items-center gap-2">
							<Badge>
								{layer.piece_count}
								{layer.piece_count === 1
									? $_('components.bins.snapshotsModal.pieceSingular')
									: $_('components.bins.snapshotsModal.piecePlural')}
							</Badge>
							<Badge>
								{$_('components.bins.snapshotsModal.emptiedAt', {
									values: { when: formatLastSeen(layer.flushed_at) }
								})}
							</Badge>
						</div>
					</div>
					{#if layer.category_ids.length > 0}
						<p class="text-sm text-ink-muted">
							{$_('components.bins.snapshotsModal.assignedList', {
								values: { list: layer.category_ids.map((id) => formatCategoryName(id) || id).join(', ') }
							})}
						</p>
					{/if}
					{#if layer.items.length > 0}
						<div class="overflow-x-auto rounded-control">
							<table class="data-table">
								<thead>
									<tr>
										<th>{$_('components.bins.snapshotsModal.partHeader')}</th>
										<th>{$_('components.bins.snapshotsModal.colorHeader')}</th>
										<th>{$_('components.bins.snapshotsModal.categoryHeader')}</th>
										<th class="num">{$_('components.bins.snapshotsModal.countHeader')}</th>
										<th>{$_('components.bins.snapshotsModal.lastSeenHeader')}</th>
									</tr>
								</thead>
								<tbody>
									{#each layer.items as item (item.item_key)}
										<tr>
											<td class="font-medium">
												{item.part_id ?? $_('components.bins.snapshotsModal.unknownPart')}
											</td>
											<td>{item.color_name ?? item.color_id ?? $_('components.bins.snapshotsModal.notAvailable')}</td>
											<td>
												{formatCategoryName(item.category_id) ||
													item.category_id ||
													$_('components.bins.snapshotsModal.notAvailable')}
											</td>
											<td class="num">{item.count}</td>
											<td>{formatLastSeen(item.last_distributed_at)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</section>
			{/each}
		</div>
	{:else if detailLoading || snapshotsLoading}
		<p class="flex items-center justify-center gap-2 py-10 text-sm text-ink-muted">
			<Spinner size={16} />
			{$_('components.bins.snapshotsModal.loadingSnapshots')}
		</p>
	{:else if snapshots.length === 0}
		<EmptyState title={$_('components.bins.snapshotsModal.noSnapshotsTitle')}>
			{$_('components.bins.snapshotsModal.noSnapshotsBody')}
		</EmptyState>
	{:else}
		<ul class="divide-y divide-line">
			{#each snapshots as snapshot (snapshot.id)}
				<li class="flex flex-wrap items-center justify-between gap-3 py-3">
					<div>
						<div class="flex items-center gap-2 text-sm font-medium text-ink">
							{formatLastSeen(snapshot.closed_at ?? snapshot.created_at)}
							{#if snapshot.status === 'open'}
								<Badge tone="primary">{$_('components.bins.snapshotsModal.accumulatingBadge')}</Badge>
							{/if}
						</div>
						<div class="num mt-0.5 text-sm text-ink-muted">
							{snapshot.piece_count}
							{snapshot.piece_count === 1
								? $_('components.bins.snapshotsModal.pieceSingular')
								: $_('components.bins.snapshotsModal.piecePlural')}
							· {snapshot.bin_count}
							{snapshot.bin_count === 1
								? $_('components.bins.snapshotsModal.binSingular')
								: $_('components.bins.snapshotsModal.binPlural')}
							· {snapshot.layer_count}
							{snapshot.layer_count === 1
								? $_('components.bins.snapshotsModal.wipeSingular')
								: $_('components.bins.snapshotsModal.wipePlural')}
						</div>
					</div>
					<div class="flex items-center gap-2">
						<Button size="sm" onclick={() => void openDetail(snapshot.id)}>
							{$_('components.bins.snapshotsModal.viewButton')}
						</Button>
						<Button size="sm" icon={Download} href={csvUrl(snapshot.id)} download>
							{$_('components.bins.snapshotsModal.csvButton')}
						</Button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</Modal>
