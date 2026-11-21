<script lang="ts">
	// The ONE classification-status badge: the records list, the recent pieces
	// and the piece detail page all render this, so the colors cannot diverge.
	// The green Classified badge is gated strictly on classification_status ===
	// 'classified': failed/unknown/not_found pieces set classified_at too, and
	// must never read as a success.
	import { _ } from 'svelte-i18n';
	import Badge from '$lib/components/ui/Badge.svelte';
	import { effectiveStatus } from '$lib/pieces';

	let {
		status,
		dead = false,
		requestFailed = false
	}: {
		status: string | null | undefined;
		dead?: boolean;
		requestFailed?: boolean;
	} = $props();

	type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger';
	type Spec = { label: string; tone: Tone; title: string | undefined };

	const spec = $derived.by<Spec>(() => {
		const s = effectiveStatus(status, requestFailed);
		if (s === 'classified')
			return { label: $_('components.pieceStatusBadge.classified'), tone: 'success', title: undefined };
		if (s === 'failed')
			return {
				label: $_('components.pieceStatusBadge.idFailed'),
				tone: 'danger',
				title: $_('components.pieceStatusBadge.idFailedTitle')
			};
		if (s === 'unknown' || s === 'not_found')
			return {
				label: $_('components.pieceStatusBadge.unidentified'),
				tone: 'warning',
				title: $_('components.pieceStatusBadge.unidentifiedTitle')
			};
		if (s === 'multi_drop_fail')
			return {
				label: $_('components.pieceStatusBadge.multiDrop'),
				tone: 'danger',
				title: $_('components.pieceStatusBadge.multiDropTitle')
			};
		if (s === 'classifying')
			return { label: $_('components.pieceStatusBadge.classifying'), tone: 'primary', title: undefined };
		if (s === 'pending')
			return { label: $_('components.pieceStatusBadge.pending'), tone: 'primary', title: undefined };
		return {
			label: s ? s.replace(/_/g, ' ') : $_('components.pieceStatusBadge.noStatus'),
			tone: 'neutral',
			title: undefined
		};
	});
</script>

<span class="inline-flex" title={spec.title}><Badge tone={spec.tone}>{spec.label}</Badge></span>
{#if dead}
	<span class="inline-flex" title={$_('components.pieceStatusBadge.timedOutTitle')}>
		<Badge tone="warning">{$_('components.pieceStatusBadge.timedOut')}</Badge>
	</span>
{/if}
