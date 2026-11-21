<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import type { UnsavedGuard } from '$lib/settings/unsavedChanges.svelte';

	// Open only while the guard holds a navigation. Three outcomes, because the
	// browser's two-button confirm forces "discard" and "cancel" to share a
	// button and quietly loses work. Closing it (Escape, the X) is "stay".
	let { guard }: { guard: UnsavedGuard } = $props();
</script>

<Modal
	open={Boolean(guard.promptUrl)}
	title={$_('components.settings.unsavedChangesDialog.title')}
	size="sm"
	onclose={() => {
		if (guard.promptUrl) guard.stay();
	}}
>
	<p class="text-ink-muted">{$_('components.settings.unsavedChangesDialog.body')}</p>
	{#if guard.error}
		<div class="mt-4"><Alert tone="danger">{guard.error}</Alert></div>
	{/if}
	{#snippet footer()}
		<Button variant="ghost" onclick={() => guard.stay()} disabled={guard.busy}
			>{$_('components.settings.unsavedChangesDialog.stayButton')}</Button
		>
		<Button variant="danger" onclick={() => guard.discardAndLeave()} disabled={guard.busy}>
			{$_('components.settings.unsavedChangesDialog.discardButton')}
		</Button>
		<Button variant="primary" onclick={() => guard.saveAndLeave()} loading={guard.busy}>
			{$_('components.settings.unsavedChangesDialog.saveAndLeaveButton')}
		</Button>
	{/snippet}
</Modal>
