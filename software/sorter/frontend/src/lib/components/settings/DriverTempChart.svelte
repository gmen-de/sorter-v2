<script lang="ts">
	// One stepper driver's temperature over time, drawn like the design
	// system's charts (docs/components.md, Data): SVG on a well, the series
	// 1.5px in the primary, gridlines in `line`, 12px muted labels. The
	// drivers are small multiples with one shared y range (yMin..yMax), so
	// the panels compare by eye and none needs a legend. The two limits are
	// dashed reference lines: where the fan starts and where the machine
	// pauses. Pointing at the plot shows the reading under the pointer.
	let {
		t,
		values,
		yMin,
		yMax,
		fanStartC,
		shutdownC
	}: {
		t: number[];
		values: (number | null)[];
		yMin: number;
		yMax: number;
		fanStartC: number;
		shutdownC: number;
	} = $props();

	const t0 = $derived(t.length ? t[0] : 0);
	const span = $derived(Math.max(1, t.length ? t[t.length - 1] - t0 : 1));

	function xOf(time: number): number {
		return t.length <= 1 ? 50 : ((time - t0) / span) * 100;
	}
	function yOf(c: number): number {
		const share = (Math.min(Math.max(c, yMin), yMax) - yMin) / Math.max(1, yMax - yMin);
		return 100 - share * 100;
	}

	// A gap in the readings (no sensor, a failed read) breaks the line.
	const path = $derived.by(() => {
		let d = '';
		let drawing = false;
		for (let i = 0; i < t.length; i++) {
			const v = values[i];
			if (v === null || v === undefined) {
				drawing = false;
				continue;
			}
			d += `${drawing ? 'L' : 'M'}${xOf(t[i]).toFixed(2)},${yOf(v).toFixed(2)}`;
			drawing = true;
		}
		return d;
	});

	const hasData = $derived(values.some((v) => v !== null && v !== undefined));

	let hover = $state<number | null>(null);
	function onMove(e: PointerEvent) {
		const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const share = (e.clientX - box.left) / Math.max(1, box.width);
		const target = t0 + share * span;
		let best: number | null = null;
		for (let i = 0; i < t.length; i++) {
			if (values[i] === null || values[i] === undefined) continue;
			if (best === null || Math.abs(t[i] - target) < Math.abs(t[best] - target)) best = i;
		}
		hover = best;
	}

	function clock(time: number): string {
		return new Date(time * 1000).toLocaleTimeString(undefined, {
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	const gridLines = $derived(
		[yMin, Math.round((yMin + yMax) / 2), yMax].filter((v, i, all) => all.indexOf(v) === i)
	);
</script>

{#if !hasData}
	<div class="flex h-24 items-center justify-center rounded-control bg-well text-sm text-ink-muted">
		No readings yet.
	</div>
{:else}
	<div class="flex gap-2">
		<div
			class="relative flex h-24 w-8 shrink-0 flex-col justify-between text-right text-xs text-ink-muted"
		>
			{#each [...gridLines].reverse() as g (g)}<span class="num leading-none">{g}°</span>{/each}
		</div>
		<div class="flex min-w-0 flex-1 flex-col gap-1">
			<div
				class="relative h-24 rounded-control bg-well"
				role="img"
				aria-label="Driver temperature over time"
				onpointermove={onMove}
				onpointerleave={() => (hover = null)}
			>
				<svg
					class="absolute inset-0 h-full w-full overflow-visible"
					viewBox="0 0 100 100"
					preserveAspectRatio="none"
				>
					{#each gridLines as g (g)}
						<line
							x1="0"
							x2="100"
							y1={yOf(g)}
							y2={yOf(g)}
							stroke="var(--line)"
							vector-effect="non-scaling-stroke"
						/>
					{/each}
					{#each [fanStartC, shutdownC] as limit (limit)}
						{#if limit > yMin && limit < yMax}
							<line
								x1="0"
								x2="100"
								y1={yOf(limit)}
								y2={yOf(limit)}
								stroke="var(--ink-muted)"
								stroke-dasharray="4 4"
								vector-effect="non-scaling-stroke"
							/>
						{/if}
					{/each}
					<path
						d={path}
						fill="none"
						stroke="var(--primary)"
						stroke-width="1.5"
						vector-effect="non-scaling-stroke"
					/>
					{#if hover !== null}
						<line
							x1={xOf(t[hover])}
							x2={xOf(t[hover])}
							y1="0"
							y2="100"
							stroke="var(--ink-muted)"
							vector-effect="non-scaling-stroke"
						/>
					{/if}
				</svg>
				{#if hover !== null}
					{@const v = values[hover] as number}
					<div
						class="pointer-events-none absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-surface"
						style:left="{xOf(t[hover])}%"
						style:top="{yOf(v)}%"
					></div>
					<div
						class="pointer-events-none absolute top-1 rounded-control bg-surface px-2 py-1 text-xs text-ink shadow-sm"
						style:left="{Math.min(xOf(t[hover]), 75)}%"
					>
						<span class="num">{v.toFixed(1)} °C</span>
						<span class="text-ink-muted">{clock(t[hover])}</span>
					</div>
				{/if}
			</div>
			<div class="flex justify-between text-xs text-ink-muted">
				<span class="num">{clock(t[0])}</span>
				<span class="num">{clock(t[t.length - 1])}</span>
			</div>
		</div>
	</div>
{/if}
