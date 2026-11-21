<script lang="ts">
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import { _ } from 'svelte-i18n';
	import Alert from '$lib/components/ui/Alert.svelte';
	import type { PieceProblem } from '$lib/pieces';

	// What went wrong with a piece on its way through the machine, as one warning
	// (an Alert): a line for each problem, oldest first. Each line is a short bold
	// name for the kind of trouble, the machine's own sentence about it (the
	// specifics and the timings are already in it), and the time of day it
	// happened, small and muted. Draws nothing for a piece with no problems.
	// `line` is the Pieces list's form: the names alone, on one line; the
	// sentences are on the piece's page, one click away.
	let {
		problems,
		layout = 'full',
		class: className = ''
	}: {
		problems: PieceProblem[] | null | undefined;
		layout?: 'full' | 'line';
		class?: string;
	} = $props();

	// A kind this page has no name for yet reads as its code in words.
	function label(kind: string): string {
		const labels: Record<string, string> = {
			timed_out: $_('components.pieces.pieceProblems.labels.timedOut'),
			track_lost: $_('components.pieces.pieceProblems.labels.trackLost'),
			late_double_feed: $_('components.pieces.pieceProblems.labels.lateDoubleFeed'),
			double_feed: $_('components.pieces.pieceProblems.labels.doubleFeed'),
			stray: $_('components.pieces.pieceProblems.labels.stray'),
			bucket_hold: $_('components.pieces.pieceProblems.labels.bucketHold'),
			eject_timeout: $_('components.pieces.pieceProblems.labels.ejectTimeout'),
			pushed_off: $_('components.pieces.pieceProblems.labels.pushedOff'),
			circling: $_('components.pieces.pieceProblems.labels.circling'),
			swept: $_('components.pieces.pieceProblems.labels.swept'),
			classify_failed: $_('components.pieces.pieceProblems.labels.classifyFailed'),
			color_fallback: $_('components.pieces.pieceProblems.labels.colorFallback')
		};
		const known = labels[kind];
		if (known) return known;
		const words = kind.replace(/_/g, ' ').trim();
		return words.charAt(0).toUpperCase() + words.slice(1);
	}

	// "6:15:48 PM", on the clock of whoever is looking.
	function timeOfDay(at: number): string {
		if (!Number.isFinite(at)) return '';
		return new Date(at * 1000).toLocaleTimeString(undefined, {
			hour: 'numeric',
			minute: '2-digit',
			second: '2-digit',
			hour12: true
		});
	}
</script>

{#if problems && problems.length > 0 && layout === 'line'}
	<p class="flex min-w-0 items-center gap-1.5 text-sm text-warning-ink {className}">
		<TriangleAlert size={14} class="shrink-0" aria-hidden="true" />
		<span class="truncate">{problems.map((p) => label(p.kind)).join(' · ')}</span>
	</p>
{:else if problems && problems.length > 0}
	<Alert tone="warning" class={className}>
		<ul class="flex flex-col gap-1">
			{#each problems as problem, i (i)}
				<li>
					<span class="font-semibold">{label(problem.kind)}</span>
					{problem.message}
					<span class="num text-xs whitespace-nowrap text-ink-muted">{timeOfDay(problem.at)}</span>
				</li>
			{/each}
		</ul>
	</Alert>
{/if}
