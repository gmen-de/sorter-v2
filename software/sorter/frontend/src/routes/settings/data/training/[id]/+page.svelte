<script lang="ts">
	import { onMount } from 'svelte';
	import { _ } from 'svelte-i18n';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Square from '@lucide/svelte/icons/square';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import { fetchLegoColors } from '$lib/pieces/colors';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import RunColors from '$lib/components/training/RunColors.svelte';
	import PieceFeed from '$lib/components/training/PieceFeed.svelte';
	import {
		PAGE_SIZE,
		endRun,
		formatDuration,
		formatTime,
		formatWhen,
		getRunPieces,
		setPieceExcluded,
		setRunExcluded,
		type PieceFilter,
		type TrainingPiece,
		type TrainingRun
	} from '$lib/training/api';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const machine = getMachineContext();

	const POLL_MS = 3000;
	const FILTERS = $derived<{ value: PieceFilter; label: string }[]>([
		{ value: 'all', label: $_('routes.settings.data.trainingDetail.filters.all') },
		{ value: 'disagree', label: $_('routes.settings.data.trainingDetail.filters.disagree') },
		{ value: 'excluded', label: $_('routes.settings.data.trainingDetail.filters.excluded') }
	]);
	const EMPTY = $derived<Record<PieceFilter, string>>({
		all: $_('routes.settings.data.trainingDetail.empty.all'),
		disagree: $_('routes.settings.data.trainingDetail.empty.disagree'),
		excluded: $_('routes.settings.data.trainingDetail.empty.excluded')
	});

	const base = $derived(machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase());

	let filter = $state<PieceFilter>('all');
	let run = $state<TrainingRun | null>(null);
	let pieces = $state<TrainingPiece[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let loadingMore = $state(false);
	let runBusy = $state(false);
	let ending = $state(false);
	let error = $state<string | null>(null);

	// While the pointer is over the pieces, a refresh waits, so nothing moves
	// under a click.
	let held = false;
	let request = 0;

	function message(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	// The first page for the filter; `quiet` refreshes without the spinner.
	async function load(quiet = false) {
		const mine = ++request;
		if (!quiet) loading = true;
		try {
			const res = await getRunPieces(base, data.id, { filter });
			if (mine !== request) return;
			run = res.run;
			pieces = res.pieces;
			total = res.total;
			error = null;
		} catch (e) {
			if (mine === request && !quiet)
				error = $_('routes.settings.data.trainingDetail.loadError', { values: { error: message(e) } });
		} finally {
			if (mine === request) loading = false;
		}
	}

	async function loadMore() {
		loadingMore = true;
		try {
			const res = await getRunPieces(base, data.id, { filter, offset: pieces.length });
			const have = new Set(pieces.map((p) => p.uuid));
			run = res.run;
			total = res.total;
			pieces = [...pieces, ...res.pieces.filter((p) => !have.has(p.uuid))];
		} catch (e) {
			error = $_('routes.settings.data.trainingDetail.loadMoreError', { values: { error: message(e) } });
		} finally {
			loadingMore = false;
		}
	}

	async function togglePiece(piece: TrainingPiece) {
		error = null;
		try {
			const next = await setPieceExcluded(base, piece.uuid, !piece.training_excluded);
			pieces = pieces.map((p) => (p.uuid === next.uuid ? next : p));
			if (run && next.training_excluded !== piece.training_excluded) {
				const step = next.training_excluded ? 1 : -1;
				run = {
					...run,
					excluded_pieces: run.excluded_pieces + step,
					labeled: run.labeled - (next.training_color_id ? step : 0)
				};
			}
		} catch (e) {
			error = message(e);
		}
	}

	async function toggleRun() {
		if (!run) return;
		runBusy = true;
		error = null;
		try {
			run = await setRunExcluded(base, run.id, !run.excluded);
		} catch (e) {
			error = message(e);
		} finally {
			runBusy = false;
		}
	}

	async function end() {
		if (!run) return;
		ending = true;
		error = null;
		try {
			await endRun(base, run.id);
			await load(true);
		} catch (e) {
			error = message(e);
		} finally {
			ending = false;
		}
	}

	// A new filter, run or machine starts from the first page.
	$effect(() => {
		void [base, data.id, filter];
		pieces = [];
		void load();
	});

	// The swatches on the pieces come from the machine's palette.
	$effect(() => {
		fetchLegoColors(base).catch(() => {});
	});

	// While the run collects, its newest pieces come in, until more are loaded.
	onMount(() => {
		const timer = setInterval(() => {
			if (run?.collecting && !held && !loading && !loadingMore && pieces.length <= PAGE_SIZE) {
				void load(true);
			}
		}, POLL_MS);
		return () => clearInterval(timer);
	});
</script>

<svelte:head><title>{$_('routes.settings.data.trainingDetail.pageTitle')}</title></svelte:head>

<PageHeader
	title={run
		? $_('routes.settings.data.trainingDetail.titleWithRun', { values: { when: formatWhen(run.started_at) } })
		: $_('routes.settings.data.trainingDetail.title')}
	description={$_('routes.settings.data.trainingDetail.description')}
>
	{#snippet actions()}
		<Button icon={ArrowLeft} href="/settings/data/training"
			>{$_('routes.settings.data.trainingDetail.backButton')}</Button
		>
		{#if run?.collecting}
			<Button icon={Square} loading={ending} onclick={end}
				>{$_('routes.settings.data.trainingDetail.endRunButton')}</Button
			>
		{/if}
		{#if run}
			<Button loading={runBusy} onclick={toggleRun}>
				{run.excluded
					? $_('routes.settings.data.trainingDetail.includeRunButton')
					: $_('routes.settings.data.trainingDetail.excludeRunButton')}
			</Button>
		{/if}
	{/snippet}
	{#if run}
		<div class="flex flex-col gap-1">
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink">
				<RunColors colors={run.colors} />
				{#if run.ended_at === null}<Badge tone="success" dot
						>{$_('routes.settings.data.trainingDetail.collectingBadge')}</Badge
					>{/if}
				{#if run.excluded}<Badge>{$_('routes.settings.data.trainingDetail.excludedBadge')}</Badge>{/if}
			</div>
			<p class="num text-sm text-ink-muted">
				{#if run.ended_at === null}
					{$_('routes.settings.data.trainingDetail.runSummaryOngoing', {
						values: {
							from: formatWhen(run.started_at),
							segments: run.segments.length,
							pieces: run.pieces.toLocaleString(),
							labeled: run.labeled.toLocaleString(),
							excludedCount: run.excluded_pieces.toLocaleString()
						}
					})}
				{:else}
					{$_('routes.settings.data.trainingDetail.runSummaryEnded', {
						values: {
							from: formatWhen(run.started_at),
							to: formatTime(run.ended_at),
							duration: formatDuration(run.ended_at - run.started_at),
							segments: run.segments.length,
							pieces: run.pieces.toLocaleString(),
							labeled: run.labeled.toLocaleString(),
							excludedCount: run.excluded_pieces.toLocaleString()
						}
					})}
				{/if}
			</p>
		</div>
	{/if}
</PageHeader>

<Panel title={$_('routes.settings.data.trainingDetail.piecesPanelTitle')} flush>
	<div class="flex flex-col gap-3 px-(--pad-panel) pb-3">
		<SegmentedControl
			bind:value={filter}
			options={FILTERS}
			label={$_('routes.settings.data.trainingDetail.filterLabel')}
			size="sm"
		/>
		{#if error}<p class="text-sm text-danger-ink">{error}</p>{/if}
		{#if loading && pieces.length === 0}
			<p class="flex items-center gap-2 text-sm text-ink-muted">
				<Spinner size={14} /> {$_('routes.settings.data.trainingDetail.loadingPieces')}
			</p>
		{:else if pieces.length === 0 && !error}
			<p class="text-sm text-ink-muted">{EMPTY[filter]}</p>
		{/if}
	</div>
	{#if pieces.length > 0 && run}
		<div
			class="border-t border-line"
			role="group"
			aria-label={$_('routes.settings.data.trainingDetail.piecesGroupLabel')}
			onpointerenter={() => (held = true)}
			onpointerleave={() => (held = false)}
		>
			<PieceFeed
				{pieces}
				segments={run.segments}
				{base}
				pad="px-(--pad-panel)"
				ontoggle={togglePiece}
			/>
		</div>
		<div class="flex items-center justify-between gap-3 border-t border-line px-(--pad-panel) py-3">
			<span class="num text-sm text-ink-muted">
				{$_('routes.settings.data.trainingDetail.shownOfTotal', {
					values: { shown: pieces.length.toLocaleString(), total: total.toLocaleString() }
				})}
			</span>
			{#if pieces.length < total}
				<Button size="sm" loading={loadingMore} onclick={loadMore}
					>{$_('routes.settings.data.trainingDetail.loadMoreButton')}</Button
				>
			{/if}
		</div>
	{/if}
</Panel>
