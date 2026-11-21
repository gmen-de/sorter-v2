<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Check from '@lucide/svelte/icons/check';
	import Panel from '$lib/components/ui/Panel.svelte';
</script>

<Panel>
	<div class="flex flex-col items-center gap-3 py-8 text-center">
		<span class="flex size-12 items-center justify-center rounded-badge bg-success-soft text-success-ink">
			<Check size={24} />
		</span>
		<p class="max-w-md text-sm text-ink-muted">
			{$_('components.setup.steps.advancedStep.readyMessage')}
		</p>
	</div>
</Panel>
