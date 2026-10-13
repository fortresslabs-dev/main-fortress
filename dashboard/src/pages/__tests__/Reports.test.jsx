import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Reports from '../Reports'

vi.mock('../../services/api')

describe('<Reports />', () => {
  it('renders the reports header', async () => {
    renderWithRouter(<Reports />)
    expect(await screen.findByRole('heading', { name: /Examination evidence/i })).toBeInTheDocument()
  })
})
