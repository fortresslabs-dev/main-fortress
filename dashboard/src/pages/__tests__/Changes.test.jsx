import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Changes from '../Changes'

vi.mock('../../services/api')

describe('<Changes />', () => {
  it('renders the regulatory change inbox header', async () => {
    renderWithRouter(<Changes />)
    expect(await screen.findByRole('heading', { name: /Regulatory change inbox/i })).toBeInTheDocument()
  })
})
