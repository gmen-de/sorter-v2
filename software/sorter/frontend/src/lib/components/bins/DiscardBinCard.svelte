<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
</script>

<!-- The virtual passthrough "bin": pieces without a matching rule fall through
     every layer's doors into the discard bucket below the machine. It is shown
     so the operator knows the output exists; it is not interactive and has no
     physical slot. -->
<Panel title={$_('components.bins.discardBinCard.panelTitle')}>
	{#snippet actions()}<Badge>{$_('components.bins.discardBinCard.virtualBadge')}</Badge>{/snippet}
	<p class="text-sm text-ink-muted">
		{$_('components.bins.discardBinCard.description')}
	</p>
</Panel>
