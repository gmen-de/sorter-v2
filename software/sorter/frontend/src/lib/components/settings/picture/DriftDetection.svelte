<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase } from '$lib/backend';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import type { CameraRole } from '$lib/settings/stations';

	type Diff = {
		key: string;
		saved: number | boolean;
		live: number | boolean;
		kind: 'number' | 'boolean';
	};

	type DiffResponse = {
		ok: boolean;
		role: string;
		source?: number | string | null;
		supported: boolean;
		saved: Record<string, number | boolean>;
		live: Record<string, number | boolean>;
		diffs: Diff[];
		message?: string;
	};

	let {
		role,
		pollMs = 10000,
		onAction
	}: {
		role: CameraRole;
		pollMs?: number;
		onAction?: (action: 'adopt' | 'restore') => void;
	} = $props();

	let open = $state(false);
	let loading = $state(false);
	let applying = $state(false);
	let error = $state<string | null>(null);
	let status = $state('');
	let diffs = $state<Diff[]>([]);
	let savedSnapshot = $state<Record<string, number | boolean>>({});
	let liveSnapshot = $state<Record<string, number | boolean>>({});
	let ignoredSignature = $state<string | null>(null);
	let timer: ReturnType<typeof setInterval> | null = null;

	function signature(ds: Diff[]): string {
		return ds
			.map((d) => `${d.key}:${d.saved}->${d.live}`)
			.sort()
			.join('|');
	}

	function formatValue(v: number | boolean): string {
		if (typeof v === 'boolean')
			return v
				? $_('components.settings.picture.driftDetection.onLabel')
				: $_('components.settings.picture.driftDetection.offLabel');
		if (Number.isInteger(v)) return String(v);
		return v.toFixed(2);
	}

	async function check(): Promise<void> {
		if (applying || open) return;
		loading = true;
		try {
			const res = await fetch(
				`${getBackendHttpBase()}/api/cameras/device-settings/${role}/diff`,
				{ cache: 'no-store' }
			);
			if (!res.ok) return;
			const data = (await res.json()) as DiffResponse;
			if (!data.supported) return;
			diffs = data.diffs ?? [];
			savedSnapshot = data.saved ?? {};
			liveSnapshot = data.live ?? {};
			const sig = signature(diffs);
			if (diffs.length > 0 && sig !== ignoredSignature) {
				open = true;
				error = null;
				status = '';
			}
		} catch {
			// swallow — drift check is best-effort
		} finally {
			loading = false;
		}
	}

	async function adopt(): Promise<void> {
		applying = true;
		error = null;
		status = '';
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/cameras/device-settings/${role}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(liveSnapshot)
			});
			if (!res.ok) throw new Error(await res.text());
			status = $_('components.settings.picture.driftDetection.liveValuesSaved');
			open = false;
			ignoredSignature = null;
			onAction?.('adopt');
		} catch (e: any) {
			error = e.message ?? $_('components.settings.picture.driftDetection.failedToAdopt');
		} finally {
			applying = false;
		}
	}

	async function restore(): Promise<void> {
		applying = true;
		error = null;
		status = '';
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/cameras/device-settings/${role}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(savedSnapshot)
			});
			if (!res.ok) throw new Error(await res.text());
			status = $_('components.settings.picture.driftDetection.savedSettingsRestored');
			open = false;
			ignoredSignature = null;
			onAction?.('restore');
		} catch (e: any) {
			error = e.message ?? $_('components.settings.picture.driftDetection.failedToRestore');
		} finally {
			applying = false;
		}
	}

	function ignore(): void {
		ignoredSignature = signature(diffs);
		open = false;
	}

	$effect(() => {
		void role;
		void pollMs;
		if (timer !== null) {
			clearInterval(timer);
			timer = null;
		}
		void check();
		timer = setInterval(() => {
			void check();
		}, pollMs);
		return () => {
			if (timer !== null) {
				clearInterval(timer);
				timer = null;
			}
		};
	});
</script>

<Modal bind:open title={$_('components.settings.picture.driftDetection.modalTitle')}>
	<div class="flex flex-col gap-3">
		<p>{$_('components.settings.picture.driftDetection.modalIntro')}</p>
		{#if error}<Alert tone="danger">{error}</Alert>{/if}
		<div class="overflow-x-auto">
			<table class="data-table">
				<thead>
					<tr
						><th>{$_('components.settings.picture.driftDetection.settingHeader')}</th><th
							class="num">{$_('components.settings.picture.driftDetection.savedHeader')}</th
						><th class="num">{$_('components.settings.picture.driftDetection.liveHeader')}</th></tr
					>
				</thead>
				<tbody>
					{#each diffs as diff}
						<tr>
							<td class="font-medium">{diff.key}</td>
							<td class="num font-mono">{formatValue(diff.saved)}</td>
							<td class="num font-mono text-warning-ink">{formatValue(diff.live)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if status}<p class="text-ink-muted">{status}</p>{/if}
	</div>
	{#snippet footer()}
		<Button variant="ghost" disabled={applying} onclick={ignore}
			>{$_('components.settings.picture.driftDetection.ignoreButton')}</Button
		>
		<Button disabled={applying} onclick={adopt}
			>{$_('components.settings.picture.driftDetection.keepLiveButton')}</Button
		>
		<Button variant="primary" disabled={applying} onclick={restore}
			>{$_('components.settings.picture.driftDetection.restoreSavedButton')}</Button
		>
	{/snippet}
</Modal>
